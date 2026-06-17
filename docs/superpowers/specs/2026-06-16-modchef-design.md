# modchef — Design Specification

*Cook EasyBuild environments from software ingredients.*

**Date:** 2026-06-16
**Status:** Approved design, ready for implementation planning

## Summary

`modchef` is a Python CLI that turns a list of requested software ingredients
(command-line tools, Python packages, R packages) into a valid, conflict-free
`module load` recipe. It does this by querying a knowledge graph (RDF/Turtle)
describing every EasyBuild module installed on the HPC: their versions,
toolchains, dependencies, and bundled extension packages.

The graph is built daily by a privileged cron job that parses the EasyBuild
easyconfig repository and is bundled into the `modchef` EasyBuild module so any
user can query it offline.

## Goals

- Given a set of ingredients, produce a minimal, ordered, co-loadable set of
  `module load` lines.
- Prefer a single compatible toolchain family; degrade gracefully to the fewest
  clusters when no single family covers everything.
- Explain *why* each module was chosen.
- Support both flag-based and `recipe.yaml` file-based invocation.
- Work entirely offline at runtime (no live `module` or network calls).

## Non-Goals

- Installing software or running EasyBuild itself.
- Modifying the user's environment directly (it prints/writes recipes; the user
  sources them).
- Resolving versions not present in the bundled graph.

## Architecture

Two decoupled halves sharing only the `.ttl` file and the schema vocabulary.

```
┌─────────────────────────────────────────────────────────────┐
│  BUILD-TIME  (cron, runs as the EasyBuild admin user, daily)                │
│                                                              │
│  /opt/easybuild/ebfiles_repo/*/*.eb                     │
│         │  EasyConfigParser (EasyBuild framework API)        │
│         ▼                                                     │
│  modchef-index  ──►  modchef.ttl   (RDF knowledge graph)     │
└─────────────────────────────────────────────────────────────┘
                              │  ttl bundled into EB module
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  RUN-TIME  (any user, `module load modchef`)                 │
│                                                              │
│  modchef cook/search/explain/...                             │
│         │  load .ttl into rdflib (in-memory), SPARQL          │
│         ▼  toolchain-cluster solver                          │
│  module load recipe  ──►  stdout | env.sh | .modules         │
└─────────────────────────────────────────────────────────────┘
```

- **`modchef-index`** — privileged build-time entry point, run by cron as
  the EasyBuild admin user. Reads every `.eb` under `/opt/easybuild/ebfiles_repo/*/*.eb`,
  parses each with the EasyBuild framework API, and emits `modchef.ttl`.
- **`modchef`** — user-facing CLI. Loads the `.ttl` into rdflib in-memory and
  answers queries via SPARQL plus the toolchain-cluster solver.

### Packaging

modchef is distributed as an **EasyBuild module** (PythonPackage-style), with
`rdflib` bundled and the `.ttl` shipped alongside, so users `module load
modchef`. The cron writes a fresh `.ttl` into a location the module reads via
`$MODCHEF_TTL`, so the graph updates daily without rebuilding the module.

### Toolchain hierarchy source

The `mc:subToolchainOf` edges are derived **programmatically from EasyBuild's own
toolchain definitions** (its toolchain classes encode that e.g. `foss` =
`GCC` + `OpenMPI` + `OpenBLAS`/`FlexiBLAS` + `FFTW` + `ScaLAPACK`, and the
subtoolchain chain down to `GCCcore`). This is authoritative and auto-updates
with new toolchain generations rather than relying on a hand-maintained table.

### TTL location resolution

At runtime the graph path is resolved from an environment variable
`$MODCHEF_TTL`, set by the EB module file, with a sensible packaged default. The
cron writes a fresh graph that the module picks up without rebuilding modchef.

## Easyconfig parsing (build-time)

The cron uses the **EasyBuild framework API** (`EasyConfigParser` / the
easyconfig loading machinery), not text or AST scraping. The cron runs in an
environment where EasyBuild is importable (it runs as the EasyBuild admin user on the HPC), so
it sees fully-resolved `name`, `version`, `versionsuffix`, `toolchain`,
`moduleclass`, `dependencies`, and `exts_list` exactly as EasyBuild does,
including `easyblock`-level defaults and templated values.

For each easyconfig the parser extracts:

- Module identity: name, version, versionsuffix, full module name, moduleclass.
- Toolchain: name + version.
- `dependencies` (and `builddependencies` are ignored for runtime recipes).
- `exts_list`: the bundled extension packages, used to know which Python/R
  packages a bundle module provides (e.g. `SciPy-bundle` provides pandas,
  numpy, scipy; `R-bundle-CRAN` provides tidyverse, data.table, ggplot2).

## Knowledge graph schema

A small custom RDF vocabulary under namespace `mc:`.

### Entities

| Entity | Properties |
|---|---|
| `mc:Module` | `mc:name`, `mc:version`, `mc:fullName` (e.g. `SAMtools/1.22-GCC-14.3.0`), `mc:moduleClass`, `mc:builtWith` → `mc:Toolchain` |
| `mc:Toolchain` | `mc:name`, `mc:version` (e.g. `foss-2025a`), `mc:subToolchainOf` → `mc:Toolchain` |
| `mc:Software` | canonical lowercase key (e.g. `samtools`) — what users type |
| `mc:Package` | Python/R extension; `mc:ecosystem` ∈ {python, r} |

### Relationships

- `mc:Module mc:providesSoftware mc:Software` — the module *is* that tool.
- `mc:Module mc:providesPackage mc:Package` — derived from `exts_list`.
- `mc:Module mc:dependsOn mc:Module` — derived from `dependencies`.
- `mc:Toolchain mc:subToolchainOf mc:Toolchain` — the compatibility hierarchy,
  e.g. `GCCcore-14.3.0 ⊂ GCC-14.3.0 ⊂ gfbf-2025a ⊂ foss-2025a`.

### Toolchain compatibility

Two modules are **co-loadable** if their toolchains share a common ancestor
within the same toolchain generation. Compatibility is computed as the
transitive closure over `mc:subToolchainOf`. A module built with
`GCCcore-14.3.0` is usable alongside one built with `foss-2025a` because they
share the same compiler base in the `2025a` generation.

## The cook solver (minimize clusters)

Given a set of requested ingredients:

1. **Candidate lookup** — for each ingredient, find all modules that provide it
   (directly as software, or as a package within a bundle). Prefer newest
   version.
2. **Compatibility graph** — relate candidates using the toolchain transitive
   closure.
3. **Single-cluster attempt** — find one toolchain generation (newest first)
   where *every* ingredient has a compatible candidate. If found, emit one clean
   recipe.
4. **Fallback partition** — if no single family covers all ingredients,
   partition them into the *fewest* compatible clusters (greedy set-cover,
   newest-first), reporting each cluster separately with a note.
5. **Dependency resolution** — resolve `dependsOn` transitively, dedupe, and
   topologically order so dependencies load before dependents. The final
   `module load` list is minimal.

Results are ranked by cluster count: 1 cluster (best) → 2 → 3 …

## CLI surface

```
modchef cook   --tools ... --python ... --r ...   # generate recipe
               [recipe.yaml]                       # file-based input
               [--toolchain GCC-14.3.0]            # pin a family
               [--name germline-qc]                # label the profile
               [--output env.sh | --serve]         # write script / print loads
               [--explain] [--dry-run]
modchef search <thing> | --python <pkg> | --r <pkg>   # find matches
modchef explain <module>     # why a module would be chosen / what it provides
modchef inspect <module>     # raw facts: version, toolchain, deps
modchef ingredients <module> # list packages a bundle provides
modchef menu                 # list all known tools/packages
```

### Command behavior

- **`cook`** — the core command. Accepts ingredients via flags
  (`--tools`/`--python`/`--r`) or a `recipe.yaml` positional argument. Runs the
  solver and emits a recipe.
  - `--toolchain GCC-14.3.0` pins the solve to a specific family.
  - `--name` labels the generated profile.
  - `--output FILE` writes a sourceable script (`env.sh`, `*.modules`); default
    is bare `module load …` lines to stdout. `--serve` is an alias for printing
    the final load commands.
  - `--explain` annotates each selection, e.g. "Selected SciPy-bundle because it
    provides: pandas, numpy, scipy".
  - `--dry-run` shows the solve and chosen modules without writing output.
- **`search`** — find modules matching a tool name, or `--python`/`--r` a
  package, with fuzzy matching.
- **`explain`** — explain why a given module would be chosen and what it
  provides.
- **`inspect`** — raw facts about a module: version, toolchain, dependencies.
- **`ingredients`** — list the packages a bundle module provides (its
  `exts_list`).
- **`menu`** — list all known tools and packages in the graph.

### recipe.yaml

Mirrors the cook flags:

```yaml
name: variant-qc
tools:
  - samtools
  - bcftools
  - mosdepth
  - multiqc
python:
  - pandas
  - numpy
  - matplotlib
r:
  - tidyverse
  - data.table
```

Used as `modchef cook recipe.yaml --output load_modules.sh`.

## Error handling

- **Ingredient not found** — suggest closest matches from the graph (fuzzy
  hint), exit non-zero.
- **No compatible cluster for an ingredient even alone** — name that ingredient
  explicitly as the blocker.
- **Stale/missing `.ttl`** — clear message pointing at `$MODCHEF_TTL` and the
  cron that maintains it.

## Testing

- **Solver and graph queries** are pure functions over a graph — unit-tested
  against a small fixture `.ttl` covering: a single-cluster solve, a
  multi-cluster fallback, a subtoolchain compatibility case, and a
  not-found/blocker case.
- **The cron parser** is tested against a handful of real `.eb` fixtures: a
  bundle with `exts_list`, a dependency chain, and a subtoolchain case.
- TDD throughout: tests precede implementation.

## Validation (2026-06-16 POC)

A throwaway proof-of-concept (`draft_ttl_poc.py`) parsed 94 real test
easyconfigs from the EasyBuild framework's bundled test set into a `.ttl` with
zero errors (990 triples; 126 modules, 22 toolchains, 30 packages). SPARQL
queries confirmed the three core relationships resolve correctly:
`providesPackage` (e.g. `Python/2.7.10-intel-2018a` → arff, cython, …),
`builtWith` toolchains, and `dependsOn` (e.g. `foss/2018a` → `FFTW/3.3.7`). This
de-risks the eb → ttl → SPARQL pipeline.

Two findings folded into the plan:

- **Use the full `EasyConfig` loader, not the raw `EasyConfigParser`.** The raw
  parser leaves templates unresolved (observed `cpe/%(version)s/EXTERNAL_MODULE`).
  The framework's higher-level `EasyConfig` object resolves templates and
  easyblock defaults — required for accurate `dependencies`/`exts_list`.
- **Filter `EXTERNAL_MODULE` dependencies** out of runtime recipes.

## Open questions

To be revisited during implementation planning:

- Exact `subToolchainOf` edge derivation for toolchain generations not
  explicitly chained in easyconfigs (likely a small known-hierarchy table for
  base toolchains like `foss`, `gfbf`, `GCC`, `GCCcore`). Confirmed by POC: the
  `.eb` files name a toolchain but do not encode the sub-toolchain chain.
- Fuzzy-match algorithm choice for `search` and not-found suggestions.
