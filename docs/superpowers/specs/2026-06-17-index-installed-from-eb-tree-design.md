# Index the Installed Tier from the EasyBuild Install Tree

## Problem

modchef builds its "installed" tier by parsing easyconfig *files* under a
curated repo (`--repo`, e.g. `ebfile_repo`). That repo does not mirror what is
actually installed on the HPC. Concrete failure: `R-bundle-CRAN` is installed
(five versions show in `ml avail`, with extensions like `ggplot2`), yet modchef
cannot find it — `search r-bundle-cran` misses, and `('ggplot2','r')` is not in
the catalog — because its easyconfig is not in `ebfile_repo`. So common
R/Bioconductor packages are unfindable, and the gap is silent: `build_graph`
swallows parse failures with no report (the code comment even claims they are
"logged in main", but nothing logs them).

The fix: source the installed tier from **what is actually installed**. Every
module on this cluster is EasyBuild-built, and EasyBuild stamps the exact
easyconfig it used into the software install tree at
`…/software/<name>/<version>/easybuild/*.eb`. Indexing those makes "installed"
authoritative and complete (including every bundle's `exts_list`), and reuses
the existing full easyconfig parser.

## Scope

- **In scope:** switch the installed tier's source to the EB software install
  tree; decouple the dependency-resolution robot path from the source dir;
  report skipped easyconfigs instead of dropping them silently.
- **Out of scope:** the available tier (`--official-repo`, full-parse,
  `installed=false`) and its dedup are unchanged; non-EasyBuild modulefiles
  (none on this cluster); Lmod spider parsing.

## Architecture

The installed tier is produced by globbing the stamped easyconfigs in the EB
software install tree and parsing them with the existing `parse_easyconfig`
(full `exts_list` + dependencies), marked `installed=true`. The available tier
is unchanged. Dependency resolution uses a robot path pointed at a full
easyconfigs collection, supplied separately from the install-tree source.

### Install-tree layout and glob

EasyBuild lays out installed software as
`<install-root>/software/<name>/<version>/easybuild/<name>-<version>-<tc>.eb`,
where that `easybuild/` directory also contains a `reprod/` subdirectory with
copies of the easyconfig **and its dependencies'** easyconfigs. The installed
glob is therefore pinned to `*/*/easybuild/*.eb` (relative to the software
root) — a non-recursive pattern that matches exactly the stamped easyconfig per
module and never descends into `reprod/` (which sits one level deeper at
`*/*/easybuild/reprod/*.eb`). Using the current recursive `**/*.eb` would pull
in the reprod dependency copies as if they were top-level installs.

## Components (`indexer.py`)

### `main()`
- Source the installed tier from an install-tree root with a fixed install-tree
  glob:
  - `--installed-root` (default `/opt/easybuild/software`): root of the EB
    software install tree.
  - `--installed-glob` (default `*/*/easybuild/*.eb`): glob, relative to
    `--installed-root`, for the stamped easyconfigs.
- `--robot-repo` (default: the `--official-repo` value if given, else
  `--installed-root`): an easyconfigs directory used **only** as the robot path
  for resolving the installed easyconfigs' dependencies. Resolving deps works
  best against a full easyconfigs collection, so the official collection is the
  natural default.
- `--official-repo` (unchanged): the available tier (full-parse,
  `installed=false`), with its robot path pointing at itself as today.
- Glob the install tree; if empty, error as today. Pass the installed robot
  path as `--robot-repo` (not the source dir).
- Pass a fresh `skipped` list into `build_graph` and print a summary afterward.

### `build_graph(paths, with_toolchain_hierarchy=False, robot_paths=None, official_paths=None, official_robot_paths=None, skipped=None)`
- New optional `skipped` parameter: a list. In **both** the installed and
  official passes, when `parse_easyconfig` raises for a path, append
  `(path, str(exc))` to `skipped` (when provided) instead of silently
  `continue`-ing. Behaviour without `skipped` is unchanged (still resilient).
- The installed pass already configures the robot path from `robot_paths`; the
  official pass already reconfigures it to `official_robot_paths`. No structural
  change — `main()` simply passes `robot_paths = --robot-repo`.

### `main()` skip summary
After `build_graph`, print:
```
modchef-index: skipped N of M easyconfigs
modchef-index:   <path>: <error>
...
```
(One line per skip; `M` = total install-tree paths considered.) This makes a
dropped `R-bundle-CRAN` visible on every cron run.

## Data flow

```
modchef-index --installed-root /opt/easybuild/software \
              --robot-repo     /opt/easybuild/easyconfigs \
              --official-repo   /opt/easybuild/easyconfigs \
              --output …/modchef.ttl

installed: glob installed-root / */*/easybuild/*.eb   (skips reprod/)
           -> parse_easyconfig (full exts/deps), installed=true
           robot path = --robot-repo
available: glob official-repo / **/*.eb -> parse, installed=false   (unchanged)
           robot path = official-repo
dedup:     official module skipped when full_name already installed (unchanged)
skips:     every unparseable easyconfig recorded in `skipped` and summarised
```

## Testing

- **glob excludes reprod:** a temp tree with
  `software/Foo/1.0/easybuild/Foo-1.0.eb` and
  `software/Foo/1.0/easybuild/reprod/Dep-1.0.eb`, globbed with
  `*/*/easybuild/*.eb`, yields only the `Foo` easyconfig; `Dep` is not present
  as a module in the resulting graph.
- **installed tier from tree carries exts:** an R-bundle-style fixture
  (`R-bundle-CRAN-2023.12-foss-2023a.eb`) with `exts_list = ['ggplot2', ...]`
  placed in the tree → the graph has that module `installed=true` and
  `providesPackage ggplot2 (ecosystem r)`.
- **skip reporting:** `build_graph(paths, skipped=skips)` over a set including a
  deliberately invalid easyconfig → `skips` contains `(path, error)` for the
  bad one and the valid ones are still indexed.
- **robot path decoupling:** `main()` passes `--robot-repo` (not the source
  dir) as the robot path — extend the existing
  `test_build_graph_configures_robot_path`/`main` robot-path coverage to assert
  the install-tree run configures EB with the `--robot-repo` value.
- **docs:** README "Daily index" section updated to the new flags.

## Fixtures

`tests/fixtures/install_tree/software/` mirroring the EB layout:
- `R-bundle-CRAN/2023.12-foss-2023a/easybuild/R-bundle-CRAN-2023.12-foss-2023a.eb`
  — `easyblock = 'Bundle'`, `exts_list` including `ggplot2`, no dependencies (so
  the test needs no robot path).
- `R-bundle-CRAN/2023.12-foss-2023a/easybuild/reprod/zlib-1.2.13-GCCcore-12.3.0.eb`
  — a reprod copy that the glob must ignore.

## Rollout

Code change in `indexer.py` (does not affect the runtime CLI), plus a cron
command change to point `--installed-root` at the software tree and
`--robot-repo`/`--official-repo` at the easyconfigs collection. Requires the
indexer redeploy and a re-index for the new installed tier to take effect; the
runtime query layer is untouched.
