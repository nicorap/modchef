# Minimal module-load output

**Date:** 2026-06-16
**Status:** Approved

## Problem

`modchef cook` emits one `module load` line for every node in the transitive
dependency tree of the chosen modules. A request like:

```
modchef cook --tools samtools --python pandas --r affy
```

produces ~120 `module load` lines. In an Lmod/EasyBuild environment this is
redundant: EasyBuild-generated Lua modulefiles load their own runtime
dependencies. Loading `SAMtools/1.22.1-GCC-14.3.0` already pulls in bzip2,
zlib, GCC, ncurses, and so on. Emitting the full transitive list is noise.

## Correctness basis

The indexer already excludes build-only dependencies
(`indexer.py:81` — `if d.get("build_only"): continue`). Therefore every
`mc:dependsOn` edge in the catalog is a **runtime** dependency, which is exactly
what an EasyBuild modulefile auto-loads. Treating a module's transitive
`dependsOn` closure as "already loaded" is safe.

## Design

### Core idea

A cluster's load set is the chosen ingredient-provider modules (one per covered
ingredient). Their transitive runtime deps are auto-loaded by Lmod and need not
be emitted. Among the chosen modules themselves, drop any that are a transitive
dependency of another chosen module. The remaining **DAG roots** are the minimal
correct set.

Example outcome (minimal):

```
module load SAMtools/1.22.1-GCC-14.3.0
module load <python module providing pandas>
module load R-bundle-Bioconductor/3.22-foss-2025b-R-4.5.2
```

If the Python module is itself a runtime dep of the Bioconductor bundle, it
collapses away too.

### Algorithm (`solver.cook`)

Per cluster:

1. Pick the toolchain root and the chosen module per covered ingredient (as today).
2. For each chosen module, compute its transitive runtime-dep closure
   (set of `full_name`s) via recursive `graph.dependencies_of`.
3. A chosen module `X` is **redundant** if some other chosen module's closure
   contains `X`. Drop redundant modules; keep the roots as `cluster.modules`.
4. When `X` is dropped, merge its `reasons` entry into the surviving root whose
   closure contains `X`, so `--explain` still shows e.g. "requested r: affy"
   next to the Bioconductor bundle.

Minimization is per-cluster; each cluster (distinct toolchain generation) is
reduced independently.

### `--full` flag (CLI)

Default output is now minimal. A new `--full` flag restores today's behavior —
the complete deps-first transitive list — for debugging or for environments
where modulefiles do not auto-load dependencies. The `--full` path reuses the
existing `_resolve_deps` walk.

### Output

`output.render` is structurally unchanged; it simply receives fewer modules.
Roots are emitted in the order their ingredients were covered (stable,
request-ordered), not deps-first — ordering is immaterial once Lmod resolves
the rest.

## Testing

Unit tests on `cook`:

- **a.** Chain A→B→C where only A is requested (B, C are deps) → emits just A.
- **b.** Two requested ingredients where one transitively provides the other →
  emits one root with merged reasons.
- **c.** `--full` still emits the whole transitive chain, deps-first.
- **d.** Multi-cluster request → each cluster minimized independently.

## Out of scope

- No change to toolchain selection / clustering logic.
- No change to the indexer or catalog schema.
- No handling of modulefiles that use `depends_on` vs `load` differently; the
  environment is assumed to auto-load runtime deps (the `--full` escape hatch
  covers the exceptions).
