# Minimal module-load output Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `modchef cook` emit only the dependency-DAG roots by default (relying on Lmod to auto-load runtime deps), with a `--full` flag to restore the complete transitive list.

**Architecture:** `solver.cook` gains a `full=False` parameter. When `full` is False (default), each cluster keeps only the chosen ingredient-provider modules that are *not* a transitive runtime dependency of another chosen module; dropped modules' `--explain` reasons merge into the surviving root. When `full` is True, the existing `_resolve_deps` transitive walk is used unchanged. The CLI adds a `--full` flag wired into `cook`.

**Tech Stack:** Python, rdflib, pytest. Fixture graph at `tests/fixtures/sample.ttl` (SAMtools→zlib, BCFtools→zlib, SciPy-bundle no deps, BWA no deps).

---

### Task 1: Minimal roots by default in `solver.cook`

**Files:**
- Modify: `src/modchef/solver.py` (the `cook` cluster-building loop, ~lines 103-116; add helpers)
- Test: `tests/test_solver.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_solver.py`:

```python
def test_minimal_drops_transitive_dep(sample_graph):
    # SAMtools depends on zlib; minimal output must NOT emit zlib.
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools")])
    names = [m.name for c in result.clusters for m in c.modules]
    assert names == ["SAMtools"]
    assert "zlib" not in names


def test_minimal_collapses_requested_dep(sample_graph):
    # Requesting both samtools and zlib: zlib is a runtime dep of SAMtools,
    # so the minimal set is just SAMtools, and its reasons carry both requests.
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools"),
                          solver.Ingredient("tool", "zlib")])
    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert [m.name for m in cluster.modules] == ["SAMtools"]
    reasons = cluster.reasons["SAMtools/1.18-GCC-12.3.0"]
    assert "requested tool: samtools" in reasons
    assert "requested tool: zlib" in reasons
```

Also UPDATE the existing `test_two_gcc_tools_form_one_cluster` — its
`assert "zlib" in names` is now wrong (zlib is a shared dep, auto-loaded).
Replace its body with:

```python
def test_two_gcc_tools_form_one_cluster(sample_graph):
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools"),
                          solver.Ingredient("tool", "bcftools")])
    assert result.unresolved == []
    assert len(result.clusters) == 1
    names = {m.name for m in result.clusters[0].modules}
    assert names == {"SAMtools", "BCFtools"}  # zlib auto-loaded, not emitted
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_solver.py -k "minimal or two_gcc" -v`
Expected: FAIL — `test_minimal_drops_transitive_dep` finds `["SAMtools", "zlib"]` (or `["zlib","SAMtools"]`), `test_two_gcc...` finds zlib in the set.

- [ ] **Step 3: Add the closure + minimize helpers**

In `src/modchef/solver.py`, add after `_resolve_deps`:

```python
def _dep_closure(graph, module):
    """All transitive runtime-dep full_names of `module` (excludes itself)."""
    out = set()
    def walk(m):
        for d in graph.dependencies_of(m.uri):
            if d.full_name not in out:
                out.add(d.full_name)
                walk(d)
    walk(module)
    return out


def _minimize(graph, chosen_for, cluster):
    """Keep only chosen modules not depended-on by another chosen module.

    `chosen_for` is a list of (ModuleRef, Ingredient) in covered order. Reasons
    for dropped modules merge into the surviving root that pulls them in.
    """
    # reasons per chosen full_name, preserving first-seen order of modules
    reasons = {}
    order = []
    closures = {}
    for chosen, ing in chosen_for:
        if chosen.full_name not in reasons:
            reasons[chosen.full_name] = []
            order.append(chosen)
            closures[chosen.full_name] = _dep_closure(graph, chosen)
        reasons[chosen.full_name].append(f"requested {ing.kind}: {ing.name}")

    roots = [m for m in order
             if not any(m.full_name in closures[other.full_name]
                        for other in order if other.full_name != m.full_name)]

    cluster.modules = list(roots)
    cluster.reasons = {r.full_name: list(reasons[r.full_name]) for r in roots}

    # fold dropped modules' reasons into a root whose closure covers them
    for m in order:
        if m in roots:
            continue
        for r in roots:
            if m.full_name in closures[r.full_name]:
                cluster.reasons[r.full_name].extend(reasons[m.full_name])
                break
```

- [ ] **Step 4: Rewrite the cluster-building body of `cook` to use `_minimize`**

Replace this block in `cook` (currently ~lines 103-115):

```python
        cluster = Cluster(toolchain_id=best_root)
        seen = set()
        for ing in covered:
            compatible_mods = [m for m in cand[ing]
                               if (m.toolchain_id in anc) or
                               (m.toolchain_id is None and best_root is None)]
            chosen = _newest(compatible_mods)
            cluster.reasons[chosen.full_name] = [
                f"requested {ing.kind}: {ing.name}"]
            for mod in _resolve_deps(graph, chosen, seen):
                if mod not in cluster.modules:
                    cluster.modules.append(mod)
        clusters.append(cluster)
```

with:

```python
        cluster = Cluster(toolchain_id=best_root)
        chosen_for = []
        for ing in covered:
            compatible_mods = [m for m in cand[ing]
                               if (m.toolchain_id in anc) or
                               (m.toolchain_id is None and best_root is None)]
            chosen_for.append((_newest(compatible_mods), ing))
        _minimize(graph, chosen_for, cluster)
        clusters.append(cluster)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_solver.py -v`
Expected: PASS (all solver tests, including the updated `test_two_gcc...`).

- [ ] **Step 6: Commit**

```bash
git add src/modchef/solver.py tests/test_solver.py
git commit -m "feat: emit only dependency-DAG roots by default in cook

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `--full` flag in `solver.cook` restores transitive list

**Files:**
- Modify: `src/modchef/solver.py` (`cook` signature + branch)
- Test: `tests/test_solver.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_solver.py`:

```python
def test_full_emits_transitive_chain(sample_graph):
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools")], full=True)
    names = [m.name for c in result.clusters for m in c.modules]
    assert names == ["zlib", "SAMtools"]  # deps-first, full transitive list
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_solver.py::test_full_emits_transitive_chain -v`
Expected: FAIL — `cook() got an unexpected keyword argument 'full'`.

- [ ] **Step 3: Add the `full` parameter and branch**

Change the signature:

```python
def cook(graph, ingredients, pinned_toolchain=None, full=False):
```

In the cluster-building body, replace the `_minimize` call from Task 1 with a
branch:

```python
        cluster = Cluster(toolchain_id=best_root)
        chosen_for = []
        for ing in covered:
            compatible_mods = [m for m in cand[ing]
                               if (m.toolchain_id in anc) or
                               (m.toolchain_id is None and best_root is None)]
            chosen_for.append((_newest(compatible_mods), ing))
        if full:
            seen = set()
            for chosen, ing in chosen_for:
                cluster.reasons.setdefault(chosen.full_name, []).append(
                    f"requested {ing.kind}: {ing.name}")
                for mod in _resolve_deps(graph, chosen, seen):
                    if mod not in cluster.modules:
                        cluster.modules.append(mod)
        else:
            _minimize(graph, chosen_for, cluster)
        clusters.append(cluster)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_solver.py -v`
Expected: PASS (full chain test plus all Task 1 tests).

- [ ] **Step 5: Commit**

```bash
git add src/modchef/solver.py tests/test_solver.py
git commit -m "feat: add full=True to cook for complete transitive list

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Wire `--full` into the cook CLI

**Files:**
- Modify: `src/modchef/cli.py` (`_cmd_cook` ~line 47, argparse ~line 176)
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
def test_cook_minimal_by_default(capsys):
    cli.main(["cook", "--tools", "samtools", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "module load SAMtools/1.18-GCC-12.3.0" in out
    assert "zlib" not in out  # dep auto-loaded by Lmod, not emitted

def test_cook_full_emits_deps(capsys):
    cli.main(["cook", "--tools", "samtools", "--full", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "module load zlib/1.2.13-GCCcore-12.3.0" in out
    assert "module load SAMtools/1.18-GCC-12.3.0" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_cli.py -k "minimal_by_default or full_emits" -v`
Expected: `test_cook_full_emits_deps` FAILs (argparse error: unrecognized `--full`); `test_cook_minimal_by_default` PASSES already (Task 1 made it minimal) — that's fine, it guards the default.

- [ ] **Step 3: Add the argparse flag**

In `src/modchef/cli.py`, after the `--dry-run` argument (~line 176):

```python
    cook.add_argument("--full", action="store_true",
                      help="Emit the complete transitive dependency list "
                           "instead of only the modules Lmod won't auto-load.")
```

- [ ] **Step 4: Pass it into `solver.cook`**

In `_cmd_cook`, change the `solver.cook(...)` call:

```python
    result = solver.cook(g, rec.ingredients, pinned_toolchain=args.toolchain,
                         full=args.full)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (both new tests and all existing cli tests).

- [ ] **Step 6: Commit**

```bash
git add src/modchef/cli.py tests/test_cli.py
git commit -m "feat: add --full flag to cook CLI

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Full suite regression check

**Files:** none (verification only)

- [ ] **Step 1: Run the whole test suite**

Run: `python -m pytest -q`
Expected: all tests PASS. If `test_output.py` or `test_recipe.py` assert on
emitted dep modules, update those assertions to the minimal expectation
(roots only) and re-run; commit any such fix with
`test: align output/recipe tests with minimal module-load default`.

- [ ] **Step 2: Manual smoke check (optional, if a real .ttl is available)**

Run: `MODCHEF_TTL=<real.ttl> python -m modchef cook --tools samtools --python pandas --r affy`
Expected: a handful of `module load` lines (roots) rather than the full tree;
adding `--full` reproduces the long list.

---

## Notes

- Minimization is per-cluster — multi-cluster requests (e.g. samtools + bwa,
  incompatible generations) already minimize independently because `_minimize`
  runs once per cluster.
- A chosen module satisfying multiple ingredients (e.g. SciPy-bundle for numpy
  and pandas) accumulates both reasons via the `reasons[...].append` path.
