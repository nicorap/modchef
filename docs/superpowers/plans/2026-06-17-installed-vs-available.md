# Installed vs. Available Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach modchef the difference between modules actually installed on the HPC and easyconfigs that merely exist in the official EasyBuild collection, so `cook` builds from installed modules first and tells the user which missing tools to request from support.

**Architecture:** One RDF graph, two tiers, distinguished by a boolean `mc:installed` on each `Module`. The installed tier (`ebfile_repo`) is parsed in full as today; the available tier (`--official-repo`) is parsed lightweight (name/version/toolchain/software only) from the easyconfig filename. `cook` prefers installed candidates, routes installed-less-but-available ingredients to a new `needs_install` bucket, and leaves genuinely-unknown ingredients in `unresolved`.

**Tech Stack:** Python 3, `rdflib`, EasyBuild framework (only at index time, on the HPC cron). `pytest` for tests.

## Global Constraints

- No new third-party dependencies. Use `rdflib`, stdlib `re`/`os`/`glob`/`argparse` only.
- EasyBuild is available only in the indexer's runtime (the cron). The runtime CLI/graph/solver/output layers MUST NOT import EasyBuild.
- `mc:installed` is a boolean literal. Absence of the predicate means **installed** (backward compatibility with graphs produced before this feature).
- Lightweight (available) nodes carry NO `mc:dependsOn` and NO `mc:providesPackage` edges.
- Predicates are accessed as `schema.MC.installed` — `MC` is an `rdflib.Namespace`, so no code change is needed in `schema.py` to "declare" the term.

---

### Task 1: `ModuleRef.installed` flag + graph reads `mc:installed` + fixture tiers

**Files:**
- Modify: `src/modchef/graph.py` (`ModuleRef` dataclass; `_module_ref`)
- Modify: `tests/fixtures/sample.ttl` (append tiered modules)
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ModuleRef` gains field `installed: bool = True` (6th field, default keeps all existing positional `ModuleRef(...)` constructions valid). `graph._module_ref` populates it from `mc:installed`, defaulting to `True` when the predicate is absent.

- [ ] **Step 1: Append tiered modules to the fixture**

Append to `tests/fixtures/sample.ttl`:

```turtle
# --- installed/available tiers (installed-vs-available feature) ---

# STAR: available in EasyBuild but NOT installed on this HPC
<https://modchef.dev/r/m-star-2-7-11a-gcc-12-3-0> a mc:Module ;
    mc:builtWith <https://modchef.dev/r/tc-gcc-12-3-0> ;
    mc:fullName "STAR/2.7.11a-GCC-12.3.0" ;
    mc:installed false ;
    mc:name "STAR" ;
    mc:providesSoftware <https://modchef.dev/r/sw-star> ;
    mc:version "2.7.11a" .

<https://modchef.dev/r/sw-star> a mc:Software ;
    mc:name "star" .

# GATK present in both tiers: installed 4.5.0.0 shadows available 4.6.0.0
<https://modchef.dev/r/m-gatk-4-5-0-0-gcc-12-3-0> a mc:Module ;
    mc:builtWith <https://modchef.dev/r/tc-gcc-12-3-0> ;
    mc:fullName "GATK/4.5.0.0-GCC-12.3.0" ;
    mc:installed true ;
    mc:name "GATK" ;
    mc:providesSoftware <https://modchef.dev/r/sw-gatk> ;
    mc:version "4.5.0.0" .

<https://modchef.dev/r/m-gatk-4-6-0-0-gcc-12-3-0> a mc:Module ;
    mc:builtWith <https://modchef.dev/r/tc-gcc-12-3-0> ;
    mc:fullName "GATK/4.6.0.0-GCC-12.3.0" ;
    mc:installed false ;
    mc:name "GATK" ;
    mc:providesSoftware <https://modchef.dev/r/sw-gatk> ;
    mc:version "4.6.0.0" .

<https://modchef.dev/r/sw-gatk> a mc:Software ;
    mc:name "gatk" .
```

- [ ] **Step 2: Write the failing test**

Add to `tests/test_graph.py`:

```python
def test_module_ref_carries_installed_flag(sample_graph):
    star = sample_graph.modules_providing("star", kind="tool")[0]
    assert star.installed is False
    sam = next(m for m in sample_graph.modules_providing("samtools", kind="tool"))
    assert sam.installed is True   # predicate absent -> default installed
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_graph.py::test_module_ref_carries_installed_flag -v`
Expected: FAIL — `AttributeError: 'ModuleRef' object has no attribute 'installed'`.

- [ ] **Step 4: Implement the flag**

In `src/modchef/graph.py`, add the field to the dataclass:

```python
@dataclass(frozen=True)
class ModuleRef:
    uri: str
    full_name: str
    name: str
    version: str
    toolchain_id: Optional[str]
    installed: bool = True
```

In `_module_ref`, read the predicate (place near the `tc_uri` lookup):

```python
    def _module_ref(self, m_uri):
        tc_uri = self.g.value(m_uri, schema.MC.builtWith)
        inst = self.g.value(m_uri, schema.MC.installed)
        return ModuleRef(
            uri=str(m_uri),
            full_name=str(self.g.value(m_uri, schema.MC.fullName)),
            name=str(self.g.value(m_uri, schema.MC.name)),
            version=str(self.g.value(m_uri, schema.MC.version)),
            toolchain_id=self._tc_id(tc_uri) if tc_uri else None,
            installed=inst.toPython() if inst is not None else True,
        )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_graph.py -v`
Expected: PASS (new test + all existing graph tests).

- [ ] **Step 6: Commit**

```bash
git add src/modchef/graph.py tests/fixtures/sample.ttl tests/test_graph.py
git commit -m "feat: ModuleRef.installed flag read from mc:installed"
```

---

### Task 2: Indexer marks the installed tier `mc:installed true`

**Files:**
- Modify: `src/modchef/indexer.py` (`_add_facts`)
- Test: `tests/test_indexer.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `_add_facts(g, facts, installed=True)` — adds `mc:installed` boolean. Default `True` preserves the existing `build_graph` installed pass.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_indexer.py`:

```python
def test_build_graph_marks_installed_true():
    files = [os.path.join(FIX, f) for f in os.listdir(FIX)]
    g = indexer.build_graph(files)
    q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "SAMtools" ; mc:installed true . }
    """
    assert bool(g.query(q))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_indexer.py::test_build_graph_marks_installed_true -v`
Expected: FAIL — ASK returns False (no `mc:installed` triple yet).

- [ ] **Step 3: Add the `installed` parameter and triple**

In `src/modchef/indexer.py`, change the `_add_facts` signature and add the triple right after the module type triple:

```python
def _add_facts(g, facts, installed=True):
    m = schema.module_uri(facts.full_name)
    g.add((m, RDF.type, schema.MC.Module))
    g.add((m, schema.MC.installed, Literal(installed)))
    g.add((m, schema.MC.name, Literal(facts.name)))
```

(Leave the rest of `_add_facts` unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indexer.py -v`
Expected: PASS (new test + existing indexer tests).

- [ ] **Step 5: Commit**

```bash
git add src/modchef/indexer.py tests/test_indexer.py
git commit -m "feat: indexer marks installed modules mc:installed true"
```

---

### Task 3: Lightweight filename parser + available pass + `--official-repo`

**Files:**
- Modify: `src/modchef/indexer.py` (new `parse_easyconfig_filename`, `_KNOWN_TOOLCHAINS`; `build_graph` `official_paths` param; `main` `--official-repo` arg)
- Test: `tests/test_indexer.py`

**Interfaces:**
- Consumes: `_add_facts(g, facts, installed=False)` from Task 2; `ModuleFacts` and `schema.full_module_name`.
- Produces:
  - `parse_easyconfig_filename(path) -> ModuleFacts` — derives name/version/toolchain from the EB filename; `dependencies=[]`, `packages=[]`, `moduleclass=""`. Falls back to the full `parse_easyconfig(path)` when the filename can't be split cleanly.
  - `build_graph(paths, with_toolchain_hierarchy=False, robot_paths=None, official_paths=None)` — after the installed pass, adds each official path lightweight as `installed=False`, skipping any whose `full_name` is already in the graph.
  - `main` accepts `--official-repo`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_indexer.py`:

```python
def test_parse_easyconfig_filename_tool():
    facts = indexer.parse_easyconfig_filename("/x/STAR-2.7.11a-GCC-12.3.0.eb")
    assert facts.name == "STAR"
    assert facts.version == "2.7.11a"
    assert facts.toolchain_name == "GCC"
    assert facts.toolchain_version == "12.3.0"
    assert facts.full_name == "STAR/2.7.11a-GCC-12.3.0"
    assert facts.dependencies == []
    assert facts.packages == []

def test_parse_easyconfig_filename_hyphenated_name():
    facts = indexer.parse_easyconfig_filename("/x/SciPy-bundle-2023.07-gfbf-2023a.eb")
    assert facts.name == "SciPy-bundle"
    assert facts.version == "2023.07"
    assert facts.toolchain_name == "gfbf"
    assert facts.toolchain_version == "2023a"

def test_official_pass_marks_not_installed_and_dedups(tmp_path):
    official = tmp_path / "official"
    official.mkdir()
    (official / "STAR-2.7.11a-GCC-12.3.0.eb").write_text("")     # official-only
    (official / "SAMtools-1.18-GCC-12.3.0.eb").write_text("")    # dup of installed
    eb_files = [os.path.join(FIX, f) for f in os.listdir(FIX)]
    g = indexer.build_graph(
        eb_files, official_paths=[str(p) for p in official.iterdir()])
    star_q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "STAR" ; mc:installed false . }
    """
    assert bool(g.query(star_q))            # STAR added as not-installed
    dup_q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "SAMtools" ; mc:installed false . }
    """
    assert not bool(g.query(dup_q))         # installed SAMtools not downgraded
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_indexer.py::test_parse_easyconfig_filename_tool -v`
Expected: FAIL — `AttributeError: module 'modchef.indexer' has no attribute 'parse_easyconfig_filename'`.

- [ ] **Step 3: Implement the filename parser**

In `src/modchef/indexer.py`, add `import re` at the top, and add near the top of the module:

```python
# EasyBuild toolchain names seen in easyconfig filenames. The lightweight
# (available) parser uses this set to split a filename's version from its
# toolchain. Filenames whose toolchain token is unknown fall back to the full
# EasyConfig parse, so this list only needs the common toolchains.
_KNOWN_TOOLCHAINS = {
    "system", "GCCcore", "GCC", "gompi", "gfbf", "foss", "fosscuda",
    "gompic", "golf", "gcccuda", "iccifort", "iimpi", "iompi", "iomkl",
    "intel", "intelcuda", "iccifortcuda", "nvompi", "nvofbf", "nvhpc",
    "gmpolf", "gimkl", "pomkl", "pompi", "gmpich",
}
```

Add the parser function (after `parse_easyconfig`):

```python
def parse_easyconfig_filename(path) -> ModuleFacts:
    """Lightweight parse: derive facts from the EB filename, no deps/packages.

    EasyBuild filenames are `Name-version-toolchainname-toolchainversion[suffix].eb`.
    The name may contain hyphens (e.g. SciPy-bundle), so the split point is the
    first hyphen that begins the version (a digit). The toolchain is located by
    matching a known toolchain name. Anything that doesn't split cleanly falls
    back to the full EasyConfig parse.
    """
    stem = os.path.basename(path)
    if stem.endswith(".eb"):
        stem = stem[:-3]
    m = re.match(r"(.+?)-(\d.*)$", stem)
    if not m:
        return parse_easyconfig(path)
    name, rest = m.group(1), m.group(2)
    parts = rest.split("-")
    tc_idx = next((i for i, p in enumerate(parts) if p in _KNOWN_TOOLCHAINS), None)
    if tc_idx is None:
        if len(parts) == 1:                       # system toolchain
            version, tc_name, tc_version, suffix = parts[0], "system", "", ""
        else:
            return parse_easyconfig(path)
    else:
        if tc_idx + 1 >= len(parts):
            return parse_easyconfig(path)
        version = "-".join(parts[:tc_idx])
        tc_name = parts[tc_idx]
        tc_version = parts[tc_idx + 1]
        suffix = "".join("-" + p for p in parts[tc_idx + 2:])
    full_name = schema.full_module_name(
        name, version, tc_name, tc_version, suffix)
    return ModuleFacts(
        name=name, version=version, versionsuffix=suffix,
        toolchain_name=tc_name, toolchain_version=tc_version,
        moduleclass="", full_name=full_name, dependencies=[], packages=[])
```

- [ ] **Step 4: Wire the available pass into `build_graph`**

Change `build_graph` in `src/modchef/indexer.py`:

```python
def build_graph(paths, with_toolchain_hierarchy=False, robot_paths=None,
                official_paths=None):
    if robot_paths:
        configure_easybuild(robot_paths)
    g = Graph()
    g.bind("mc", schema.MC)
    for path in paths:
        try:
            _add_facts(g, parse_easyconfig(path))
        except Exception:
            continue   # resilient: skip unparseable configs (logged in main)
    if official_paths:
        installed_names = {str(o) for o in g.objects(None, schema.MC.fullName)}
        for path in official_paths:
            try:
                facts = parse_easyconfig_filename(path)
            except Exception:
                continue
            if facts.full_name in installed_names:
                continue   # already installed; do not downgrade to available
            _add_facts(g, facts, installed=False)
    if with_toolchain_hierarchy:
        _add_toolchain_hierarchy(g)
    return g
```

- [ ] **Step 5: Add the `--official-repo` flag to `main`**

In `main`, add the argument and pass discovered paths:

```python
    parser.add_argument("--official-repo", default=None,
                        help="Root of the official EasyBuild easyconfig "
                             "collection (parsed lightweight as 'available').")
    args = parser.parse_args(argv)

    paths = glob.glob(os.path.join(args.repo, args.glob), recursive=True)
    if not paths:
        print(f"modchef-index: no easyconfigs under {args.repo}", file=sys.stderr)
        return 1
    official = None
    if args.official_repo:
        official = glob.glob(
            os.path.join(args.official_repo, args.glob), recursive=True)
    g = build_graph(paths, with_toolchain_hierarchy=True,
                    robot_paths=os.path.abspath(args.repo),
                    official_paths=official)
```

(Leave the `serialize`/print lines below unchanged.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_indexer.py -v`
Expected: PASS (3 new tests + existing indexer tests).

- [ ] **Step 7: Commit**

```bash
git add src/modchef/indexer.py tests/test_indexer.py
git commit -m "feat: index official easyconfigs as available (not-installed) tier"
```

---

### Task 4: Solver routes available-only ingredients to `needs_install`

**Files:**
- Modify: `src/modchef/solver.py` (`CookResult`; `cook` candidate phase)
- Test: `tests/test_solver.py`

**Interfaces:**
- Consumes: `ModuleRef.installed` (Task 1).
- Produces: `CookResult` gains `needs_install: list` of `(Ingredient, list[ModuleRef])`. `cook` partitions per-ingredient candidates: installed candidates shadow available ones; ingredients with only available candidates go to `needs_install`; ingredients with no candidate at all stay in `unresolved`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_solver.py`:

```python
def test_available_only_goes_to_needs_install(sample_graph):
    res = solver.cook(sample_graph, [solver.Ingredient("tool", "star")])
    assert res.clusters == []
    names = [ing.name for ing, mods in res.needs_install]
    assert "star" in names
    assert solver.Ingredient("tool", "star") not in res.unresolved

def test_installed_shadows_available(sample_graph):
    res = solver.cook(sample_graph, [solver.Ingredient("tool", "gatk")])
    assert res.needs_install == []
    loaded = [m.full_name for c in res.clusters for m in c.modules]
    assert "GATK/4.5.0.0-GCC-12.3.0" in loaded
    assert "GATK/4.6.0.0-GCC-12.3.0" not in loaded

def test_missing_everywhere_still_unresolved(sample_graph):
    res = solver.cook(sample_graph, [solver.Ingredient("tool", "nope")])
    assert solver.Ingredient("tool", "nope") in res.unresolved
    assert res.needs_install == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_solver.py::test_available_only_goes_to_needs_install -v`
Expected: FAIL — `AttributeError: 'CookResult' object has no attribute 'needs_install'`.

- [ ] **Step 3: Add the `needs_install` field**

In `src/modchef/solver.py`:

```python
@dataclass
class CookResult:
    clusters: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    needs_install: list = field(default_factory=list)
```

- [ ] **Step 4: Partition candidates in `cook`**

Replace the candidate-lookup block at the top of `cook` (the `for ing in ingredients:` loop and the `unresolved = []` line) with:

```python
    cand = {}
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
        elif available:
            needs_install.append((ing, available))
        else:
            unresolved.append(ing)
```

And change the final return:

```python
    return CookResult(clusters=clusters, unresolved=unresolved,
                      needs_install=needs_install)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_solver.py -v`
Expected: PASS (3 new tests + all existing solver tests).

- [ ] **Step 6: Commit**

```bash
git add src/modchef/solver.py tests/test_solver.py
git commit -m "feat: cook routes available-only ingredients to needs_install"
```

---

### Task 5: Output renders the request-install section

**Files:**
- Modify: `src/modchef/output.py` (`render`)
- Test: `tests/test_output.py`

**Interfaces:**
- Consumes: `CookResult.needs_install` (Task 4); `ModuleRef.installed` (Task 1).
- Produces: `render` emits, after the cluster load lines, one `# REQUEST INSTALL:` block per `needs_install` entry.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_output.py`:

```python
def test_needs_install_warning():
    avail = ModuleRef("u", "STAR/2.7.11a-GCC-12.3.0", "STAR", "2.7.11a",
                      "GCC-12.3.0", installed=False)
    res = CookResult(clusters=[], unresolved=[],
                     needs_install=[(Ingredient("tool", "star"), [avail])])
    text = output.render(res)
    assert "STAR/2.7.11a-GCC-12.3.0" in text
    assert "ask support" in text.lower()
    assert "not installed" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_output.py::test_needs_install_warning -v`
Expected: FAIL — STAR full name not present in output.

- [ ] **Step 3: Render the section**

In `src/modchef/output.py`, insert before the `for ing in result.unresolved:` loop:

```python
    for ing, mods in result.needs_install:
        names = ", ".join(sorted(m.full_name for m in mods))
        lines.append(
            f"# REQUEST INSTALL: {ing.name} ({ing.kind}) is available in "
            f"EasyBuild but not installed on this HPC.")
        lines.append(f"#   ask support to install: {names}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_output.py -v`
Expected: PASS (new test + existing output tests).

- [ ] **Step 5: Commit**

```bash
git add src/modchef/output.py tests/test_output.py
git commit -m "feat: render request-install section for needs_install"
```

---

### Task 6: CLI — cook warning/exit code + search annotation

**Files:**
- Modify: `src/modchef/cli.py` (`_cmd_cook`, `_cmd_search`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `CookResult.needs_install` (Task 4); `ModuleRef.installed` (Task 1).
- Produces: `cook` prints an "ask support to install" line to stderr per `needs_install` entry and exits nonzero when `needs_install` or `unresolved` is non-empty. `search` annotates each result with `[installed]` or `[available — not installed]`, installed first.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_cook_needs_install_warns_and_exits_nonzero(capsys):
    rc = cli.main(["cook", "--tools", "star", "--graph", FIX_TTL])
    captured = capsys.readouterr()
    assert rc == 1
    assert "ask support to install" in captured.err
    assert "STAR/2.7.11a-GCC-12.3.0" in captured.err

def test_search_annotates_installed(capsys):
    cli.main(["search", "samtools", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "SAMtools/1.18-GCC-12.3.0" in out
    assert "[installed]" in out

def test_search_annotates_available(capsys):
    cli.main(["search", "star", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "available — not installed" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py::test_cook_needs_install_warns_and_exits_nonzero tests/test_cli.py::test_search_annotates_available -v`
Expected: FAIL — no ask-support line on stderr; no annotation in search output.

- [ ] **Step 3: Add the cook warning + exit code**

In `src/modchef/cli.py`, in `_cmd_cook`, replace the trailing unresolved-hint block and return:

```python
    # 'did you mean' hints go to stderr so stdout stays a clean recipe
    for ing in result.unresolved:
        print(f"modchef: {ing.name} ({ing.kind}) not found", file=sys.stderr)
        _suggest(ing.name, _candidate_names(g, ing.kind))
    for ing, mods in result.needs_install:
        names = ", ".join(sorted(m.full_name for m in mods))
        print(f"modchef: {ing.name} ({ing.kind}) is available in EasyBuild but "
              f"not installed; ask support to install: {names}", file=sys.stderr)
    return 1 if (result.unresolved or result.needs_install) else 0
```

- [ ] **Step 4: Annotate search results**

In `_cmd_search`, replace the result-printing loop:

```python
    for m in sorted(mods, key=lambda x: (not x.installed, x.full_name)):
        tag = "installed" if m.installed else "available — not installed"
        print(f"{m.full_name}  [{tag}]")
    return 0
```

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (all tests, including the three new CLI tests).

- [ ] **Step 6: Commit**

```bash
git add src/modchef/cli.py tests/test_cli.py
git commit -m "feat: cook warns on needs_install; search annotates installed vs available"
```

---

### Task 7: Document the new tier in README

**Files:**
- Modify: `README.md`
- Test: none (docs).

- [ ] **Step 1: Update the daily-index section**

In `README.md`, update the index invocation to show `--official-repo` and add a one-line note on the tiers:

```markdown
## Daily index (cron, runs as the EasyBuild admin user)

    modchef-index --repo /opt/easybuild/ebfiles_repo \
                  --official-repo /opt/easybuild/easyconfigs \
                  --output /opt/easybuild/modchef/modchef.ttl

`--repo` is the set installed on the HPC (full facts, cookable). `--official-repo`
is the official EasyBuild collection, indexed lightweight as "available, not
installed": `cook` builds from installed modules first and, when a tool is only
available, tells you the easyconfig to ask support to install.
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document --official-repo and the installed/available tiers"
```

---

## Self-Review

**Spec coverage:**
- `mc:installed` predicate + backward-compat default → Tasks 1, 2.
- Installed tier full parse marked installed → Task 2.
- Available tier lightweight filename parse, no deps/packages → Task 3.
- `--official-repo` second pass into one .ttl → Task 3.
- Dedup (installed wins) → Task 3 (`installed_names` skip).
- `ModuleRef.installed` + `_module_ref` default True → Task 1.
- `cook` prefers installed, `needs_install` bucket, packages-with-no-installed → unresolved → Task 4 (package case falls out: available tier has no `providesPackage`, so `_candidates` returns nothing for a package kind → unresolved).
- Output request-install section → Task 5.
- CLI cook stderr + nonzero exit; search annotation → Task 6.
- `menu`/`explain`/`inspect` unchanged → not touched (correct).
- README → Task 7.

**Placeholder scan:** none — every code step shows full code.

**Type consistency:** `ModuleRef.installed: bool = True` defined in Task 1, used in Tasks 4/5/6. `CookResult.needs_install: list` of `(Ingredient, list[ModuleRef])` defined in Task 4, consumed identically in Tasks 5/6. `parse_easyconfig_filename(path) -> ModuleFacts` and `build_graph(..., official_paths=None)` defined and used consistently in Task 3.
