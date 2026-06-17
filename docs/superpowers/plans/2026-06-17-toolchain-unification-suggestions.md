# Toolchain Unification Suggestions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When cook splits requested tools across more than one toolchain cluster, search the installed+available candidates for a single toolchain that covers them all and suggest the installs needed to unify them.

**Architecture:** A new pure function `solver._unify` searches candidate toolchain generations for one whose ancestor closure covers every clustered ingredient (using installed or available builds), choosing fewest-installs-then-newest. `cook` calls it only when installed-only clustering yields >1 cluster and stores the result on `CookResult.unification`; `output`/`cli` render it as an informational suggestion. It is orthogonal to `needs_install` and does not change exit codes.

**Tech Stack:** Python 3, `rdflib` (graph only), `pytest`.

## Global Constraints

- No new third-party dependencies.
- Runtime layers (solver/graph/output/cli) MUST NOT import EasyBuild.
- Unification is orthogonal to `needs_install`: ingredients with no installed build anywhere stay in `needs_install` and are excluded from the unification search.
- Unification is informational: it never changes cook's exit code (nonzero only for `unresolved`/`needs_install`, as today).
- Selection rule: fewest installs, then newest generation (`natural_key`).
- System toolchain (`toolchain_id is None`) generations are skipped as unification targets (Conda/system handled by a later feature).

---

### Task 1: `Unification` model + `_unify` search function

**Files:**
- Modify: `src/modchef/solver.py` (add `Unification`, add `CookResult.unification`, add `_unify`)
- Test: `tests/test_solver.py`

**Interfaces:**
- Consumes: existing `ModuleRef` (`from modchef.graph import ModuleRef`), `Ingredient`, `_newest`, `natural_key`.
- Produces:
  - `Unification(toolchain_id: str, installs: list, reused: list)` — `installs`/`reused` are lists of `(Ingredient, ModuleRef)`.
  - `CookResult.unification: Optional[Unification] = None`.
  - `_unify(graph, full_cand) -> Optional[Unification]` where `full_cand` is `{Ingredient: [ModuleRef, ...]}` (installed + available candidates). `graph` only needs a `compatible_toolchains(tc_id) -> set[str]` method.

- [ ] **Step 1: Write the failing unit tests (stub graph, no rdflib needed)**

Add to `tests/test_solver.py`:

```python
from modchef.graph import ModuleRef


class _StubGraph:
    """Minimal graph exposing only compatible_toolchains for _unify tests."""
    def __init__(self, hierarchy):
        self._h = hierarchy

    def compatible_toolchains(self, tc_id):
        return self._h[tc_id]


def _ref(name, tc, installed, version="1.0"):
    return ModuleRef(f"u-{name}-{tc}", f"{name}/{version}-{tc}",
                     name, version, tc, installed)


def test_unify_picks_newer_on_install_tie():
    g = _StubGraph({"A-1": {"A-1"}, "A-2": {"A-2"}})
    p, q = solver.Ingredient("tool", "p"), solver.Ingredient("tool", "q")
    full_cand = {
        p: [_ref("P", "A-1", True), _ref("P", "A-2", False)],
        q: [_ref("Q", "A-2", True), _ref("Q", "A-1", False)],
    }
    u = solver._unify(g, full_cand)
    assert u is not None
    assert u.toolchain_id == "A-2"            # tie on installs (1 each) -> newer
    assert len(u.installs) == 1

def test_unify_prefers_fewer_installs_over_newer():
    g = _StubGraph({"A-1": {"A-1"}, "A-2": {"A-2"}})
    r = solver.Ingredient("tool", "r")
    s = solver.Ingredient("tool", "s")
    t = solver.Ingredient("tool", "t")
    full_cand = {
        r: [_ref("R", "A-1", True), _ref("R", "A-2", False)],
        s: [_ref("S", "A-1", True), _ref("S", "A-2", False)],
        t: [_ref("T", "A-2", True), _ref("T", "A-1", False)],
    }
    u = solver._unify(g, full_cand)
    assert u.toolchain_id == "A-1"            # 1 install vs 2 -> fewer wins
    assert [m.full_name for _, m in u.installs] == ["T/1.0-A-1"]

def test_unify_returns_none_when_no_generation_covers_all():
    g = _StubGraph({"A-1": {"A-1"}, "A-2": {"A-2"}})
    a, b = solver.Ingredient("tool", "a"), solver.Ingredient("tool", "b")
    full_cand = {
        a: [_ref("A", "A-1", True)],          # only old, nothing on A-2
        b: [_ref("B", "A-2", True)],          # only new, nothing on A-1
    }
    assert solver._unify(g, full_cand) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_solver.py::test_unify_picks_newer_on_install_tie -v`
Expected: FAIL — `AttributeError: module 'modchef.solver' has no attribute '_unify'`.

- [ ] **Step 3: Add the `Unification` dataclass and `CookResult` field**

In `src/modchef/solver.py`, after the `Cluster` dataclass add:

```python
@dataclass
class Unification:
    toolchain_id: str
    installs: list = field(default_factory=list)   # [(Ingredient, ModuleRef)]
    reused: list = field(default_factory=list)     # [(Ingredient, ModuleRef)]
```

And extend `CookResult`:

```python
@dataclass
class CookResult:
    clusters: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    needs_install: list = field(default_factory=list)
    unification: "Optional[Unification]" = None
```

- [ ] **Step 4: Implement `_unify`**

In `src/modchef/solver.py`, add (e.g. just before `cook`):

```python
def _unify(graph, full_cand):
    """Find one toolchain generation covering every clustered ingredient.

    `full_cand` maps each clustered ingredient to its installed+available
    candidates. A generation G works if every ingredient has a candidate whose
    toolchain is in compatible_toolchains(G). For such a G, ingredients with no
    installed G-compatible build (but an available one) become installs; the
    rest are reused. Returns the G needing the fewest installs (newest on ties),
    or None when no generation covers everyone.
    """
    ings = list(full_cand.keys())
    roots = {m.toolchain_id for ing in ings for m in full_cand[ing]
             if m.toolchain_id is not None}

    options = []
    for g_root in roots:
        anc = graph.compatible_toolchains(g_root)
        installs, reused = [], []
        covered = True
        for ing in ings:
            compat = [m for m in full_cand[ing] if m.toolchain_id in anc]
            if not compat:
                covered = False
                break
            inst = [m for m in compat if m.installed]
            if inst:
                reused.append((ing, _newest(inst)))
            else:
                installs.append((ing, _newest([m for m in compat])))
        if not covered or not installs:
            continue   # no installs => not a split; nothing to suggest
        options.append(Unification(g_root, installs, reused))

    if not options:
        return None
    options.sort(key=lambda u: natural_key(u.toolchain_id), reverse=True)  # newest first
    options.sort(key=lambda u: len(u.installs))                            # then fewest installs (stable)
    return options[0]
```

- [ ] **Step 5: Run the unit tests to verify they pass**

Run: `python -m pytest tests/test_solver.py -k unify -v`
Expected: PASS (3 new tests).

- [ ] **Step 6: Commit**

```bash
git add src/modchef/solver.py tests/test_solver.py
git commit -m "feat: add _unify search for a single covering toolchain generation"
```

---

### Task 2: Wire `_unify` into `cook` + fixtures + integration test

**Files:**
- Modify: `src/modchef/solver.py` (`cook`: retain full candidates, call `_unify` on splits)
- Modify: `tests/fixtures/sample.ttl` (add ToolX/ToolY split-then-unify case)
- Test: `tests/test_solver.py`

**Interfaces:**
- Consumes: `_unify` (Task 1), `CookResult.unification` (Task 1).
- Produces: `cook` sets `result.unification` when `len(clusters) > 1` and a unifying generation exists.

- [ ] **Step 1: Add the split-then-unify fixture**

Append to `tests/fixtures/sample.ttl`:

```turtle
# Toolchain-unification case: ToolX is installed only on the older GCC-12.3.0
# and available (not installed) on GCC-13.2.0; ToolY is installed only on
# GCC-13.2.0. Requesting both splits installed-only, and unifies under
# GCC-13.2.0 by installing the available ToolX.
<https://modchef.dev/r/m-toolx-1-0-gcc-12-3-0> a mc:Module ;
    mc:builtWith <https://modchef.dev/r/tc-gcc-12-3-0> ;
    mc:fullName "ToolX/1.0-GCC-12.3.0" ;
    mc:installed true ;
    mc:name "ToolX" ;
    mc:providesSoftware <https://modchef.dev/r/sw-toolx> ;
    mc:version "1.0" .

<https://modchef.dev/r/m-toolx-2-0-gcc-13-2-0> a mc:Module ;
    mc:builtWith <https://modchef.dev/r/tc-gcc-13-2-0> ;
    mc:fullName "ToolX/2.0-GCC-13.2.0" ;
    mc:installed false ;
    mc:name "ToolX" ;
    mc:providesSoftware <https://modchef.dev/r/sw-toolx> ;
    mc:version "2.0" .

<https://modchef.dev/r/m-tooly-1-0-gcc-13-2-0> a mc:Module ;
    mc:builtWith <https://modchef.dev/r/tc-gcc-13-2-0> ;
    mc:fullName "ToolY/1.0-GCC-13.2.0" ;
    mc:installed true ;
    mc:name "ToolY" ;
    mc:providesSoftware <https://modchef.dev/r/sw-tooly> ;
    mc:version "1.0" .

<https://modchef.dev/r/sw-toolx> a mc:Software ;
    mc:name "toolx" .

<https://modchef.dev/r/sw-tooly> a mc:Software ;
    mc:name "tooly" .
```

- [ ] **Step 2: Write the failing integration test**

Add to `tests/test_solver.py`:

```python
def test_cook_sets_unification_on_split(sample_graph):
    res = solver.cook(sample_graph, [solver.Ingredient("tool", "toolx"),
                                     solver.Ingredient("tool", "tooly")])
    assert len(res.clusters) == 2            # installed-only splits across lines
    u = res.unification
    assert u is not None
    assert u.toolchain_id == "GCC-13.2.0"
    assert [m.full_name for _, m in u.installs] == ["ToolX/2.0-GCC-13.2.0"]
    assert [m.full_name for _, m in u.reused] == ["ToolY/1.0-GCC-13.2.0"]

def test_cook_no_unification_for_single_cluster(sample_graph):
    res = solver.cook(sample_graph, [solver.Ingredient("tool", "samtools"),
                                     solver.Ingredient("tool", "bcftools")])
    assert len(res.clusters) == 1
    assert res.unification is None
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_solver.py::test_cook_sets_unification_on_split -v`
Expected: FAIL — `res.unification` is `None` (cook doesn't compute it yet).

- [ ] **Step 4: Retain full candidates and call `_unify` in `cook`**

In `src/modchef/solver.py`, in `cook`, change the candidate-partition block to also keep the full candidate list:

```python
    cand = {}
    full_cand = {}
    unresolved = []
    needs_install = []
    for ing in ingredients:
        c = _candidates(graph, ing)
        if pinned_toolchain:
            c = [m for m in c
                 if m.toolchain_id in graph.compatible_toolchains(pinned_toolchain)]
        installed = [m for m in c if m.installed]
        available = [m for m in c if not m.installed]
        if installed:
            cand[ing] = installed
            full_cand[ing] = c
        elif available:
            needs_install.append((ing, available))
        else:
            unresolved.append(ing)
```

Then change the final ranking/return to compute unification:

```python
    # rank clusters: largest first (best result = single cluster)
    clusters.sort(key=lambda c: -len(c.modules))

    unification = None
    if len(clusters) > 1:
        unification = _unify(graph, full_cand)

    return CookResult(clusters=clusters, unresolved=unresolved,
                      needs_install=needs_install, unification=unification)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest tests/test_solver.py -v`
Expected: PASS (new tests + all existing solver tests).

- [ ] **Step 6: Commit**

```bash
git add src/modchef/solver.py tests/fixtures/sample.ttl tests/test_solver.py
git commit -m "feat: cook computes a unification suggestion when tools split"
```

---

### Task 3: Render the TO-UNIFY block in output

**Files:**
- Modify: `src/modchef/output.py` (`render`)
- Test: `tests/test_output.py`

**Interfaces:**
- Consumes: `CookResult.unification` (Task 1), `Unification` (Task 1).
- Produces: `render` appends a `# TO UNIFY …` block after the cluster loads when `result.unification` is set.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_output.py` (top imports already include `Cluster, CookResult, Ingredient`; add `Unification`):

```python
from modchef.solver import Cluster, CookResult, Ingredient, Unification

def test_unification_block_rendered():
    mods = [ModuleRef("u", "ToolY/1.0-GCC-13.2.0", "ToolY", "1.0", "GCC-13.2.0")]
    c1 = Cluster(toolchain_id="GCC-13.2.0", modules=mods)
    c2 = Cluster(toolchain_id="GCC-12.3.0", modules=[
        ModuleRef("v", "ToolX/1.0-GCC-12.3.0", "ToolX", "1.0", "GCC-12.3.0")])
    install = ModuleRef("w", "ToolX/2.0-GCC-13.2.0", "ToolX", "2.0",
                        "GCC-13.2.0", installed=False)
    u = Unification(toolchain_id="GCC-13.2.0",
                    installs=[(Ingredient("tool", "toolx"), install)],
                    reused=[(Ingredient("tool", "tooly"), mods[0])])
    res = CookResult(clusters=[c1, c2], unification=u)
    text = output.render(res)
    assert "TO UNIFY" in text
    assert "GCC-13.2.0" in text
    assert "ToolX/2.0-GCC-13.2.0" in text
    assert "toolx" in text and "tooly" in text
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_output.py::test_unification_block_rendered -v`
Expected: FAIL — "TO UNIFY" not in output.

- [ ] **Step 3: Render the block**

In `src/modchef/output.py`, insert before the `for ing, mods in result.needs_install:` loop:

```python
    u = result.unification
    if u is not None:
        names = sorted({ing.name for ing, _ in (u.installs + u.reused)})
        lines.append(
            f"# TO UNIFY these {len(result.clusters)} clusters under "
            f"{u.toolchain_id}, ask support to install:")
        for ing, mod in u.installs:
            lines.append(f"#   {mod.full_name}    ({ing.kind}: {ing.name})")
        lines.append(
            f"# then all of [{', '.join(names)}] load under {u.toolchain_id}.")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_output.py -v`
Expected: PASS (new test + existing output tests).

- [ ] **Step 5: Commit**

```bash
git add src/modchef/output.py tests/test_output.py
git commit -m "feat: render TO-UNIFY suggestion block in cook output"
```

---

### Task 4: CLI stderr suggestion + exit-code-unchanged test

**Files:**
- Modify: `src/modchef/cli.py` (`_cmd_cook`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `CookResult.unification` (Task 1).
- Produces: `_cmd_cook` prints a one-line unification suggestion to stderr; exit code unchanged (nonzero only for `unresolved`/`needs_install`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_cli.py`:

```python
def test_cook_unification_suggestion_and_exit_zero(capsys):
    rc = cli.main(["cook", "--tools", "toolx", "tooly", "--graph", FIX_TTL])
    captured = capsys.readouterr()
    assert rc == 0                               # recipe loads; suggestion is informational
    assert "to unify" in captured.err.lower()
    assert "ToolX/2.0-GCC-13.2.0" in captured.err
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_cli.py::test_cook_unification_suggestion_and_exit_zero -v`
Expected: FAIL — no "to unify" text on stderr.

- [ ] **Step 3: Print the suggestion in `_cmd_cook`**

In `src/modchef/cli.py`, in `_cmd_cook`, after the `needs_install` stderr loop and before the `return` line, add:

```python
    if result.unification:
        u = result.unification
        names = ", ".join(sorted(m.full_name for _, m in u.installs))
        print(f"modchef: to unify under {u.toolchain_id}, ask support to "
              f"install: {names}", file=sys.stderr)
```

(Leave the existing `return 1 if (result.unresolved or result.needs_install) else 0` unchanged — unification does not affect the exit code.)

- [ ] **Step 4: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (all tests, including the new CLI test).

- [ ] **Step 5: Commit**

```bash
git add src/modchef/cli.py tests/test_cli.py
git commit -m "feat: cook prints toolchain-unification suggestion to stderr"
```

---

## Self-Review

**Spec coverage:**
- Trigger only on `len(clusters) > 1` → Task 2 (`if len(clusters) > 1`).
- Orthogonal to `needs_install` (excluded from search) → Task 2 (`full_cand` only filled for ingredients with installed candidates; `needs_install` untouched).
- Unifying-generation definition + installs/reused + fewest-installs-then-newest → Task 1 (`_unify`).
- System (`None`) generations skipped → Task 1 (`if m.toolchain_id is not None`).
- `Unification` model + `CookResult.unification` → Task 1.
- Keep working recipe + append block → Task 3.
- CLI stderr line, exit code unchanged → Task 4.
- Fixtures (split-then-unify) → Task 2.
- Tests: solver tie-breaks/none/single-cluster/integration, output block, cli stderr+exit → Tasks 1,2,3,4.

**Placeholder scan:** none — every code step shows full code.

**Type consistency:** `Unification(toolchain_id, installs, reused)` with `installs`/`reused` as `[(Ingredient, ModuleRef)]` is defined in Task 1 and consumed identically in Tasks 3/4. `_unify(graph, full_cand)` signature and the `full_cand` shape (`{Ingredient: [ModuleRef]}`) match between Task 1 and Task 2. `CookResult.unification` default `None` is set in Task 1 and read in 2/3/4.

**Note on `_newest([m for m in compat])` in installs:** at that branch all `compat` are available (no installed), so `_newest` over them picks the newest available build — correct.
