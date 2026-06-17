# Index the Installed Tier from the EB Install Tree — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Source modchef's installed tier from the EasyBuild software install tree (the stamped easyconfigs) so the catalog matches what is actually installed — including every bundle's `exts_list` — and report skipped easyconfigs instead of dropping them silently.

**Architecture:** `build_graph` gains an optional `skipped` list it appends parse failures to (both passes). `main()` is rewired to glob the install tree (`--installed-root` + a fixed `*/*/easybuild/*.eb` glob), resolve dependencies against a decoupled `--robot-repo` (defaulting to the official collection), keep the unchanged `--official-repo` available tier, and print a skip summary. The runtime query layer is untouched.

**Tech Stack:** Python 3, `rdflib`, EasyBuild framework (index-time only), `pytest`.

## Global Constraints

- No new third-party dependencies.
- Runtime layers (graph/solver/output/cli) MUST NOT import EasyBuild; only the indexer does.
- Installed-tier glob is `*/*/easybuild/*.eb` (non-recursive) so the `reprod/` subdirectory's dependency copies are never indexed as top-level installs.
- The dependency-resolution robot path is decoupled from the source dir: `--robot-repo` (default: the `--official-repo` value if given, else `--installed-root`).
- The available tier (`--official-repo`, full-parse, `installed=false`) and dedup are unchanged.
- Skip reporting must be backward-compatible: `build_graph` without a `skipped` list behaves exactly as today.

---

### Task 1: `build_graph` records skipped easyconfigs

**Files:**
- Modify: `src/modchef/indexer.py` (`build_graph`)
- Test: `tests/test_indexer.py`

**Interfaces:**
- Consumes: existing `parse_easyconfig`, `_add_facts`.
- Produces: `build_graph(paths, with_toolchain_hierarchy=False, robot_paths=None, official_paths=None, official_robot_paths=None, skipped=None)`. When `skipped` is a list, every easyconfig that raises during parse (in either pass) appends `(path, str(exc))` to it; valid easyconfigs are still indexed. `skipped=None` keeps current silent-skip behaviour.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_indexer.py`:

```python
def test_build_graph_reports_skipped(tmp_path):
    bad = tmp_path / "Broken-1.0.eb"
    bad.write_text("this is = not = a valid easyconfig =")
    good = os.path.join(FIX, "BWA-0.7.18-GCC-13.2.0.eb")
    skipped = []
    g = indexer.build_graph([str(bad), good], skipped=skipped)
    assert any("Broken-1.0.eb" in path for path, _ in skipped)
    q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "BWA" . }
    """
    assert bool(g.query(q))            # the valid one is still indexed
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_indexer.py::test_build_graph_reports_skipped -v`
Expected: FAIL — `TypeError: build_graph() got an unexpected keyword argument 'skipped'`.

- [ ] **Step 3: Add the `skipped` parameter and record failures**

In `src/modchef/indexer.py`, change `build_graph` to:

```python
def build_graph(paths, with_toolchain_hierarchy=False, robot_paths=None,
                official_paths=None, official_robot_paths=None, skipped=None):
    if robot_paths:
        configure_easybuild(robot_paths)
    g = Graph()
    g.bind("mc", schema.MC)
    for path in paths:
        try:
            _add_facts(g, parse_easyconfig(path))
        except Exception as exc:
            if skipped is not None:
                skipped.append((path, str(exc)))
            continue   # resilient: skip unparseable configs (recorded in `skipped`)
    if official_paths:
        # Full-parse the official collection (deps + exts) as the available
        # tier. Point the robot path at the official repo so its deps resolve
        # against its own easyconfigs, then skip anything already installed.
        if official_robot_paths:
            configure_easybuild(official_robot_paths)
        installed_names = {str(o) for o in g.objects(None, schema.MC.fullName)}
        for path in official_paths:
            try:
                facts = parse_easyconfig(path)
            except Exception as exc:
                if skipped is not None:
                    skipped.append((path, str(exc)))
                continue   # resilient: skip unparseable configs
            if facts.full_name in installed_names:
                continue   # already installed; do not downgrade to available
            _add_facts(g, facts, installed=False)
    if with_toolchain_hierarchy:
        _add_toolchain_hierarchy(g)
    return g
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_indexer.py::test_build_graph_reports_skipped -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/modchef/indexer.py tests/test_indexer.py
git commit -m "feat: build_graph records skipped easyconfigs instead of dropping silently"
```

---

### Task 2: Rewire `main()` to the install tree + decoupled robot path + skip summary

**Files:**
- Modify: `src/modchef/indexer.py` (`main`)
- Create: `tests/fixtures/install_tree/software/...` (see Step 1)
- Modify: `tests/test_indexer.py` (replace `test_main_writes_ttl`; add install-tree + robot-repo tests)
- Modify: `README.md` (Daily index section)

**Interfaces:**
- Consumes: `build_graph(..., skipped=...)` (Task 1).
- Produces: `modchef-index` CLI with `--installed-root` (default `/opt/easybuild/software`), `--installed-glob` (default `*/*/easybuild/*.eb`), `--robot-repo` (default: `--official-repo` value, else `--installed-root`), `--official-repo` (unchanged), `--output`. Prints a skip summary to stderr when any easyconfig was skipped.

- [ ] **Step 1: Create the install-tree fixtures**

Create `tests/fixtures/install_tree/software/R-bundle-CRAN/2023.12-foss-2023a/easybuild/R-bundle-CRAN-2023.12-foss-2023a.eb`:

```python
easyblock = 'Bundle'
name = 'R-bundle-CRAN'
version = '2023.12'
homepage = 'https://cran.r-project.org'
description = 'A bundle of CRAN R packages'
toolchain = {'name': 'foss', 'version': '2023a'}
exts_defaultclass = 'RPackage'
exts_list = [
    ('Rcpp', '1.0.11'),
    ('ggplot2', '3.4.4'),
]
moduleclass = 'lang'
```

Create `tests/fixtures/install_tree/software/R-bundle-CRAN/2023.12-foss-2023a/easybuild/reprod/zlib-1.2.13-GCCcore-12.3.0.eb` (a reprod copy the glob must ignore):

```python
easyblock = 'ConfigureMake'
name = 'zlib'
version = '1.2.13'
homepage = 'https://www.zlib.net/'
description = 'Compression library'
toolchain = {'name': 'GCCcore', 'version': '12.3.0'}
moduleclass = 'lib'
```

Create `tests/fixtures/install_tree/software/BWA/0.7.18-GCC-12.3.0/easybuild/BWA-0.7.18-GCC-12.3.0.eb`:

```python
easyblock = 'ConfigureMake'
name = 'BWA'
version = '0.7.18'
homepage = 'http://bio-bwa.sourceforge.net/'
description = 'Burrows-Wheeler Aligner'
toolchain = {'name': 'GCC', 'version': '12.3.0'}
moduleclass = 'bio'
```

- [ ] **Step 2: Write the failing tests (replace `test_main_writes_ttl`)**

In `tests/test_indexer.py`, **delete** `test_main_writes_ttl` and add:

```python
INSTALL_TREE = os.path.join(os.path.dirname(__file__), "fixtures",
                            "install_tree", "software")


def _reset_eb():
    indexer.configure_easybuild()
    indexer._CONFIGURED = False


def test_main_indexes_install_tree(tmp_path):
    out = tmp_path / "modchef.ttl"
    try:
        rc = indexer.main(["--installed-root", INSTALL_TREE, "--output", str(out)])
    finally:
        _reset_eb()
    assert rc == 0
    g = Graph()
    g.parse(str(out), format="turtle")
    # R-bundle-CRAN is indexed as installed, with its ext ggplot2 (ecosystem r)
    q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "R-bundle-CRAN" ; mc:installed true ; mc:providesPackage ?p .
          ?p mc:name "ggplot2" ; mc:ecosystem "r" . }
    """
    assert bool(g.query(q))
    # the reprod/ copy must NOT be indexed as a module
    q2 = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "zlib" . }
    """
    assert not bool(g.query(q2))


def test_main_uses_robot_repo_for_robot_path(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(indexer, "configure_easybuild",
                        lambda robot_paths=None: calls.append(robot_paths))
    out = tmp_path / "o.ttl"
    indexer.main(["--installed-root", INSTALL_TREE, "--robot-repo", "/robots",
                  "--output", str(out)])
    assert "/robots" in calls
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python -m pytest tests/test_indexer.py::test_main_indexes_install_tree -v`
Expected: FAIL — `error: unrecognized arguments: --installed-root` (the flag does not exist yet).

- [ ] **Step 4: Rewire `main()`**

In `src/modchef/indexer.py`, replace the body of `main()` (the argument definitions through `return 0`) with:

```python
def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="modchef-index",
        description="Parse EasyBuild easyconfigs into a modchef RDF graph.")
    parser.add_argument("--installed-root", default="/opt/easybuild/software",
                        help="Root of the EasyBuild software install tree "
                             "(installed tier).")
    parser.add_argument("--installed-glob", default="*/*/easybuild/*.eb",
                        help="Glob (relative to --installed-root) for the "
                             "stamped easyconfigs; excludes reprod/ copies.")
    parser.add_argument("--robot-repo", default=None,
                        help="Easyconfigs dir used as the robot path to resolve "
                             "installed deps (default: --official-repo, else "
                             "--installed-root).")
    parser.add_argument("--official-repo", default=None,
                        help="Root of the official EasyBuild easyconfig "
                             "collection (the available, not-installed tier).")
    parser.add_argument("--output", required=True, help="Output .ttl path.")
    args = parser.parse_args(argv)

    paths = glob.glob(os.path.join(args.installed_root, args.installed_glob),
                      recursive=True)
    if not paths:
        print(f"modchef-index: no easyconfigs under {args.installed_root}",
              file=sys.stderr)
        return 1

    official = None
    official_root = None
    if args.official_repo:
        official = glob.glob(
            os.path.join(args.official_repo, "**/*.eb"), recursive=True)
        official_root = os.path.abspath(args.official_repo)

    robot_repo = args.robot_repo or args.official_repo or args.installed_root
    skipped = []
    g = build_graph(paths, with_toolchain_hierarchy=True,
                    robot_paths=os.path.abspath(robot_repo),
                    official_paths=official, official_robot_paths=official_root,
                    skipped=skipped)
    g.serialize(args.output, format="turtle")
    considered = len(paths) + (len(official) if official else 0)
    print(f"modchef-index: wrote {len(g)} triples from {len(paths)} installed "
          f"files to {args.output}")
    if skipped:
        print(f"modchef-index: skipped {len(skipped)} of {considered} "
              f"easyconfigs", file=sys.stderr)
        for path, err in skipped:
            print(f"modchef-index:   {path}: {err}", file=sys.stderr)
    return 0
```

- [ ] **Step 5: Run the indexer tests to verify they pass**

Run: `python -m pytest tests/test_indexer.py -v`
Expected: PASS (new install-tree + robot-repo tests, plus the existing `build_graph`-based tests which are unaffected).

- [ ] **Step 6: Update the README Daily index section**

In `README.md`, replace the Daily index command and note with:

```markdown
## Daily index (cron, runs as the EasyBuild admin user)

`modchef-index` needs the EasyBuild framework importable, so the cron loads the
EasyBuild module first (the runtime `modchef` module deliberately does *not*
depend on EasyBuild, so `cook`/`search` users stay lean):

    module load EasyBuild/5.2.0
    modchef-index --installed-root /opt/easybuild/software \
                  --robot-repo     /opt/easybuild/easyconfigs \
                  --official-repo  /opt/easybuild/easyconfigs \
                  --output /opt/easybuild/modchef/modchef.ttl

`--installed-root` is the EasyBuild software install tree: modchef indexes the
easyconfig EasyBuild stamped for each *actually installed* module
(`<name>/<version>/easybuild/*.eb`), so the catalog matches what `module avail`
shows, including every bundle's extensions. `--robot-repo` is an easyconfigs
collection used only to resolve installed dependencies. `--official-repo` is the
official EasyBuild collection, indexed as "available, not installed": `cook`
builds from installed modules first and, when a tool or package is only
available, tells you the easyconfig to ask support to install. Any easyconfig
that fails to parse is reported on stderr rather than silently dropped.
```

- [ ] **Step 7: Run the full suite**

Run: `python -m pytest -q`
Expected: PASS (all tests).

- [ ] **Step 8: Commit**

```bash
git add src/modchef/indexer.py tests/test_indexer.py tests/fixtures/install_tree README.md
git commit -m "feat: index installed tier from the EB install tree; report skips"
```

---

## Self-Review

**Spec coverage:**
- Installed tier from EB install tree (`--installed-root` + `*/*/easybuild/*.eb`) → Task 2 (main rewire) + Task 2 fixtures/test.
- Glob excludes `reprod/` → Task 2 (fixed glob; `test_main_indexes_install_tree` asserts reprod zlib absent).
- Decoupled robot path (`--robot-repo`, default official-repo else installed-root) → Task 2 (`robot_repo = ...`; `test_main_uses_robot_repo_for_robot_path`).
- Available tier + dedup unchanged → Task 1/2 leave the official pass intact.
- Skip reporting (`skipped` list + summary) → Task 1 (`build_graph`) + Task 2 (`main` summary).
- Installed tier carries exts (ggplot2) → Task 2 (`test_main_indexes_install_tree`).
- README/cron docs updated → Task 2 Step 6.

**Placeholder scan:** none — every code/step shows full content.

**Type consistency:** `build_graph(..., skipped=None)` with `skipped` a list of `(path, str)` is defined in Task 1 and consumed by `main` in Task 2 (`skipped = []`, iterated as `for path, err in skipped`). New flags (`--installed-root`, `--installed-glob`, `--robot-repo`) are defined and used consistently within Task 2. `--repo`/`--glob` are removed in Task 2 and the only test using them (`test_main_writes_ttl`) is replaced in the same task.

**Note:** existing `build_graph`-based indexer tests (`test_build_graph_*`, `test_parse_*`) pass explicit path lists and are unaffected by the `main()` flag changes; only `test_main_writes_ttl` referenced `--repo` and is replaced.
