# Correct Dependency Resolution in `modchef-index` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `modchef-index` point EasyBuild's robot search path at the indexed repo, so EB's authoritative minimal-toolchain resolver returns correct sub-toolchain dependency names and complete toolchain hierarchies — eliminating the ~13% of dangling `mc:dependsOn` edges that silently truncate cooked recipes.

**Architecture:** A single root cause (no robot path) produces both observed bugs. The fix adds an EB configuration helper that sets `--robot-paths=<repo>` with `reconfigure=True`, threads a `robot_paths` argument through `build_graph`, and has `main()` pass `args.repo`. No new RDF schema, no manual resolution post-pass — EB does the resolution. A small, optional bundle-coverage salvage (`mc:coveredByBundle`) is added only if a re-measurement shows it is worthwhile.

**Tech Stack:** Python 3.13, `rdflib`, `easybuild-framework` (+ `easybuild-easyblocks`), `pytest`.

**Spec:** `docs/superpowers/specs/2026-06-16-dependency-resolution-design.md`

---

## Key facts established during the spike (do not re-derive)

- Parsing `igraph-0.10.17-foss-2025a` with **no** robot path yields a dangling
  `zlib/1.3.1-foss-2025a`; with `--robot-paths=<repo>` it yields the real
  `zlib/1.3.1-GCCcore-14.2.0`. EB's own `ec.dependencies()` does the resolution.
- EB configuration is a **global singleton**: a second `set_up_configuration`
  call is ignored unless `reconfigure=True` is passed. `reconfigure=True` after
  an ambient config *does* change the robot path (verified).
- Read the active robot path with
  `from easybuild.tools.config import build_option; build_option("robot_path")`
  → returns a list of paths.
- An **incomplete** robot path makes EB log a `WARNING` and fall back to the
  parent toolchain (it does not raise). So correctness depends on the full repo
  being on the robot path. The `tests/fixtures/eb` dir is incomplete (lacks
  toolchain closures), so it cannot serve as a hermetic newer-toolchain fixture
  — newer-toolchain correctness is validated against the real repo instead.
- `tests/fixtures/eb` contains: `BCFtools-1.18-GCC-12.3.0.eb`,
  `BWA-0.7.18-GCC-13.2.0.eb`, `SAMtools-1.18-GCC-12.3.0.eb`,
  `SciPy-bundle-2023.07-gfbf-2023a.eb`, `zlib-1.2.13-GCCcore-12.3.0.eb`.

---

## File structure

| File | Responsibility | Change |
|---|---|---|
| `src/modchef/indexer.py` | Build-time graph construction + CLI | Add `configure_easybuild(robot_paths)`, thread `robot_paths` through `build_graph`, set it in `main()`. Optional Part B salvage. |
| `tests/test_indexer.py` | Indexer unit tests | Add wiring test for robot-path configuration; (Part B) bundle-coverage test. |
| `src/modchef/schema.py` | RDF vocabulary | (Part B only) reference `MC.coveredByBundle` — no code change needed; `MC` is a `Namespace`, attributes are dynamic. |

No changes to `graph.py`, `solver.py`, `toolchains.py` (its lazy `resolve_chain`
config is harmless once `main()` has already configured EB with the robot path
first; the first-call/reconfigure behaviour is handled in the indexer).

---

## Task 1: EB configuration helper with robot path

**Files:**
- Modify: `src/modchef/indexer.py` (the `_ensure_configured` area, lines ~15-23)
- Test: `tests/test_indexer.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_indexer.py`:

```python
def test_configure_easybuild_sets_robot_path(tmp_path):
    from easybuild.tools.config import build_option
    repo = tmp_path / "repo"
    repo.mkdir()
    indexer.configure_easybuild(str(repo))
    assert str(repo) in build_option("robot_path")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_indexer.py::test_configure_easybuild_sets_robot_path -v`
Expected: FAIL with `AttributeError: module 'modchef.indexer' has no attribute 'configure_easybuild'`

- [ ] **Step 3: Write minimal implementation**

In `src/modchef/indexer.py`, replace the existing config block:

```python
_CONFIGURED = False


def _ensure_configured():
    global _CONFIGURED
    if not _CONFIGURED:
        from easybuild.tools.options import set_up_configuration
        set_up_configuration(args=[], silent=True)
        _CONFIGURED = True
```

with:

```python
_CONFIGURED = False


def configure_easybuild(robot_paths=None):
    """Configure EasyBuild, pointing the robot search path at `robot_paths`.

    The robot path is what lets EB's minimal-toolchain resolver map a dependency
    like ('zlib', '1.3.1') under a foss-2025a recipe to the real sub-toolchain
    module zlib/1.3.1-GCCcore-14.2.0. EB configuration is a global singleton, so
    reconfigure=True is required for the robot path to take effect even if an
    earlier call already configured EB.
    """
    global _CONFIGURED
    from easybuild.tools.options import set_up_configuration
    args = [f"--robot-paths={robot_paths}"] if robot_paths else []
    set_up_configuration(args=args, silent=True, reconfigure=True)
    _CONFIGURED = True


def _ensure_configured():
    if not _CONFIGURED:
        configure_easybuild()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_indexer.py::test_configure_easybuild_sets_robot_path -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/modchef/indexer.py tests/test_indexer.py
git commit -m "feat: add configure_easybuild helper that sets EB robot path"
```

---

## Task 2: Thread `robot_paths` through `build_graph` and `main`

**Files:**
- Modify: `src/modchef/indexer.py` (`build_graph` ~157-167, `main` ~170-189)
- Test: `tests/test_indexer.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_indexer.py` (uses `unittest.mock` to assert wiring without
needing a full toolchain closure on disk):

```python
def test_build_graph_configures_robot_path(monkeypatch):
    called = {}

    def fake_configure(robot_paths=None):
        called["robot_paths"] = robot_paths

    monkeypatch.setattr(indexer, "configure_easybuild", fake_configure)
    # no files -> build_graph still configures, then iterates nothing
    indexer.build_graph([], robot_paths="/some/repo")
    assert called["robot_paths"] == "/some/repo"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_indexer.py::test_build_graph_configures_robot_path -v`
Expected: FAIL — `build_graph()` got an unexpected keyword argument `robot_paths` (TypeError)

- [ ] **Step 3: Write minimal implementation**

In `src/modchef/indexer.py`, change `build_graph`:

```python
def build_graph(paths, with_toolchain_hierarchy=False, robot_paths=None):
    if robot_paths:
        configure_easybuild(robot_paths)
    g = Graph()
    g.bind("mc", schema.MC)
    for path in paths:
        try:
            _add_facts(g, parse_easyconfig(path))
        except Exception:
            continue   # resilient: skip unparseable configs (logged in main)
    if with_toolchain_hierarchy:
        _add_toolchain_hierarchy(g)
    return g
```

and in `main`, change the `build_graph` call to pass the repo as robot path:

```python
    g = build_graph(paths, with_toolchain_hierarchy=True,
                    robot_paths=os.path.abspath(args.repo))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_indexer.py::test_build_graph_configures_robot_path -v`
Expected: PASS

- [ ] **Step 5: Run the full indexer + graph suite to confirm no regression**

Run: `python3 -m pytest tests/test_indexer.py tests/test_graph.py -q`
Expected: PASS (all existing tests, including `test_parse_tool_extracts_dependency`
and `test_main_writes_ttl`, still pass).

- [ ] **Step 6: Commit**

```bash
git add src/modchef/indexer.py tests/test_indexer.py
git commit -m "feat: index against the repo's own robot path for correct dep resolution"
```

---

## Task 3: Real-repo validation and dangling-edge re-measurement

This task produces no committed code — it gathers the evidence that the fix
works and decides whether Task 4 (bundle coverage) is worth doing.

**Files:** none (validation only)

- [ ] **Step 1: Re-index the real repo**

Run:
```bash
cd /Users/nrapin/Documents/repo/modchef
python3 -m modchef.indexer --repo ../eb_gen/easybuild/easyconfigs --output /tmp/graph_fixed.ttl
```
Expected: prints `modchef-index: wrote <N> triples from <M> files`.

- [ ] **Step 2: Measure dangling edges before/after**

Run:
```bash
python3 - <<'PY'
from rdflib import Graph, URIRef
MC="https://modchef.dev/schema#"
RT=URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
Module=URIRef(MC+"Module"); dep=URIRef(MC+"dependsOn"); full=URIRef(MC+"fullName")
for label,path in [("OLD","graphttl"),("FIXED","/tmp/graph_fixed.ttl")]:
    g=Graph(); g.parse(path,format="turtle")
    mods=set(g.subjects(RT,Module))
    edges=list(g.triples((None,dep,None)))
    dangling=[d for _,_,d in edges if d not in mods]
    print(f"{label}: {len(edges)} edges, {len(dangling)} dangling "
          f"({100*len(dangling)/max(len(edges),1):.1f}%)")
PY
```
Expected: FIXED shows a large drop in dangling edges vs OLD (target: only
genuinely-external deps remain).

- [ ] **Step 3: Confirm newer-toolchain hierarchy is now complete**

Run:
```bash
MODCHEF_TTL=/tmp/graph_fixed.ttl python3 -c "
from modchef.graph import ModChefGraph
g=ModChefGraph.load('/tmp/graph_fixed.ttl')
for tc in ('foss-2025a','foss-2025b'):
    anc=sorted(g.compatible_toolchains(tc))
    print(tc, '->', anc)
    assert any('GCCcore' in a for a in anc), f'{tc} still has no sub-toolchains'
print('OK: newer toolchains have sub-toolchain chains')
"
```
Expected: `foss-2025a`/`foss-2025b` now include their `GCCcore-*`/`GCC-*`
sub-toolchains; assertion passes.

- [ ] **Step 4: Measure bundle-coverage salvage potential and decide**

Run:
```bash
python3 - <<'PY'
from rdflib import Graph, URIRef
MC="https://modchef.dev/schema#"
RT=URIRef("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
Module=URIRef(MC+"Module"); Package=URIRef(MC+"Package")
dep=URIRef(MC+"dependsOn"); full=URIRef(MC+"fullName"); name=URIRef(MC+"name")
g=Graph(); g.parse("/tmp/graph_fixed.ttl",format="turtle")
mods=set(g.subjects(RT,Module))
pkg_names={str(g.value(p,name)).lower() for p in g.subjects(RT,Package)
           if g.value(p,name)}
unresolved_names=set()
for _,_,d in g.triples((None,dep,None)):
    if d in mods: continue
    f=g.value(d,full)
    if f is None: continue
    unresolved_names.add(str(f).split("/")[0])
salvageable={n for n in unresolved_names if n.lower() in pkg_names}
print("distinct unresolved dep names:", len(unresolved_names))
print("salvageable via bundle coverage:", len(salvageable), sorted(salvageable))
PY
```
Decision rule: if `salvageable` is a meaningful count (say ≥10 names) **and**
they are real bundle-provided packages (not version mismatches), proceed to
Task 4. Otherwise STOP here, mark Task 4 as dropped (YAGNI), and record the
numbers in the plan/commit message.

- [ ] **Step 5: Record the decision**

Append a one-line note to the spec's validation section (the measured drop and
the bundle-coverage decision), commit:
```bash
git add docs/superpowers/specs/2026-06-16-dependency-resolution-design.md
git commit -m "docs: record measured dangling-edge drop and bundle-coverage decision"
```

---

## Task 4 (conditional — only if Task 3 Step 4 says yes): bundle-coverage salvage

Adds a `mc:coveredByBundle` edge from a depending module to a bundle that
provides an otherwise-unresolvable dependency as a package, in a compatible
toolchain.

**Files:**
- Modify: `src/modchef/indexer.py` (new `_add_bundle_coverage(g)`, call it from
  `build_graph` after `_add_toolchain_hierarchy`)
- Test: `tests/test_indexer.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_indexer.py`. This builds a tiny in-memory graph and runs the
salvage pass directly, so it needs no easyconfig parsing:

```python
def test_bundle_coverage_links_unresolved_dep_to_bundle():
    from rdflib import Graph, Literal, RDF
    from modchef import schema
    g = Graph()
    # A consumer module that depends on a stub (no real Module for it)
    consumer = schema.module_uri("App/1.0-foss-2023a")
    g.add((consumer, RDF.type, schema.MC.Module))
    g.add((consumer, schema.MC.fullName, Literal("App/1.0-foss-2023a")))
    tc = schema.toolchain_uri("foss-2023a")
    g.add((consumer, schema.MC.builtWith, tc))
    g.add((tc, schema.MC.toolchainId, Literal("foss-2023a")))
    stub = schema.module_uri("numpy/1.25-foss-2023a")
    g.add((stub, schema.MC.fullName, Literal("numpy/1.25-foss-2023a")))
    g.add((consumer, schema.MC.dependsOn, stub))
    # A bundle that provides numpy as a package, same toolchain
    bundle = schema.module_uri("SciPy-bundle/2023.07-foss-2023a")
    g.add((bundle, RDF.type, schema.MC.Module))
    g.add((bundle, schema.MC.builtWith, tc))
    pkg = schema.package_uri("numpy", "python")
    g.add((pkg, schema.MC.name, Literal("numpy")))
    g.add((bundle, schema.MC.providesPackage, pkg))

    indexer._add_bundle_coverage(g)

    assert (consumer, schema.MC.coveredByBundle, bundle) in g
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_indexer.py::test_bundle_coverage_links_unresolved_dep_to_bundle -v`
Expected: FAIL — `module 'modchef.indexer' has no attribute '_add_bundle_coverage'`

- [ ] **Step 3: Write minimal implementation**

Add to `src/modchef/indexer.py`:

```python
def _add_bundle_coverage(g):
    """Salvage unresolvable deps: link a depending module to a bundle that
    provides the dep's name as a package, in a compatible toolchain.

    Only deps whose target is a bare stub (no rdf:type mc:Module) are considered.
    """
    from modchef import toolchains as tc_mod

    modules = set(g.subjects(RDF.type, schema.MC.Module))

    # toolchain hierarchy {tc_id: {sub_tc_ids}} from subToolchainOf edges
    hierarchy = {}
    for parent, _, child in g.triples((None, schema.MC.subToolchainOf, None)):
        pid = g.value(parent, schema.MC.toolchainId)
        cid = g.value(child, schema.MC.toolchainId)
        if pid is not None and cid is not None:
            hierarchy.setdefault(str(pid), set()).add(str(cid))

    def tc_id_of(m):
        t = g.value(m, schema.MC.builtWith)
        return str(g.value(t, schema.MC.toolchainId)) if t is not None else None

    # index: package_name (lower) -> [(bundle_module, bundle_tc_id)]
    providers = {}
    for bundle in g.subjects(schema.MC.providesPackage, None):
        btc = tc_id_of(bundle)
        for pkg in g.objects(bundle, schema.MC.providesPackage):
            pname = g.value(pkg, schema.MC.name)
            if pname is not None:
                providers.setdefault(str(pname).lower(), []).append((bundle, btc))

    for consumer, _, target in g.triples((None, schema.MC.dependsOn, None)):
        if target in modules:
            continue  # already resolves to a real module
        full = g.value(target, schema.MC.fullName)
        if full is None:
            continue
        dep_name = str(full).split("/")[0].lower()
        cands = providers.get(dep_name)
        if not cands:
            continue
        ctc = tc_id_of(consumer)
        compat = tc_mod.ancestors(ctc, hierarchy) if ctc else {None}
        for bundle, btc in cands:
            if btc == ctc or btc in compat:
                g.add((consumer, schema.MC.coveredByBundle, bundle))
                break
```

and call it from `build_graph`, right after the hierarchy is built:

```python
    if with_toolchain_hierarchy:
        _add_toolchain_hierarchy(g)
        _add_bundle_coverage(g)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_indexer.py::test_bundle_coverage_links_unresolved_dep_to_bundle -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/modchef/indexer.py tests/test_indexer.py
git commit -m "feat: add coveredByBundle salvage for unresolvable dependencies"
```

---

## Task 5: Final verification

**Files:** none

- [ ] **Step 1: Full test suite**

Run: `python3 -m pytest -q`
Expected: all pass.

- [ ] **Step 2: Re-index real repo and spot-check a known case**

Run:
```bash
python3 -m modchef.indexer --repo ../eb_gen/easybuild/easyconfigs --output /tmp/graph_final.ttl
python3 -c "
from modchef.graph import ModChefGraph
g=ModChefGraph.load('/tmp/graph_final.ttl')
m=g.modules_by_full_name('igraph/0.10.17-foss-2025a')
deps=[d.full_name for d in g.dependencies_of(m.uri)] if m else []
print('igraph deps:', deps)
assert any('zlib/1.3.1-GCCcore' in d for d in deps), 'zlib still dangling'
print('OK: zlib resolves to its GCCcore sub-toolchain module')
"
```
Expected: `igraph` deps include `zlib/1.3.1-GCCcore-14.2.0`; assertion passes.

- [ ] **Step 3: Verify finishing-a-development-branch options**

Invoke the `superpowers:finishing-a-development-branch` skill to decide how to
integrate the `fix-dependency-resolution` branch (merge / PR / cleanup).

---

## Self-review notes

- **Spec coverage:** Part A (robot path → both bugs) = Tasks 1+2; hierarchy
  completeness validated in Task 3 Step 3; Part B (bundle coverage) = Task 4,
  gated by Task 3 Step 4 per the spec's "validate before keeping". Validation
  section = Tasks 3 and 5.
- **Dropped from pre-spike draft:** no `mc:versionSuffix`, no two-pass manual
  resolver, no static family-map fallback — superseded by the robot-path fix.
- **Type/name consistency:** `configure_easybuild(robot_paths)`,
  `build_graph(paths, with_toolchain_hierarchy=False, robot_paths=None)`,
  `_add_bundle_coverage(g)`, `mc:coveredByBundle` used consistently throughout.
