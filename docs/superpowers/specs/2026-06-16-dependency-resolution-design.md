# Correct dependency resolution in `modchef-index`

**Date:** 2026-06-16
**Status:** Approved (design); implementation pending

## Summary

`modchef-index` produces an RDF graph whose `mc:dependsOn` edges are ~13%
broken: they point at module URIs that don't exist as real `mc:Module` nodes.
Because `solver._resolve_deps` walks these edges recursively to build a cooked
recipe's transitive closure, every broken edge silently truncates a recipe.
This spec fixes dependency resolution at index time, fixes the prerequisite
toolchain-hierarchy gap that blocks it, and adds a small bundle-coverage
salvage for deps that have no standalone module.

## Problem

Two coupled bugs, both originating in `src/modchef/indexer.py`.

### Bug 1 — Incomplete toolchain hierarchy (prerequisite)

`_add_toolchain_hierarchy` builds `mc:subToolchainOf` edges by calling
`toolchains.resolve_chain`, which calls EasyBuild's `get_toolchain_hierarchy`.
That function needs the toolchain's own `.eb` on EasyBuild's robot search path
to resolve its component versions. The indexer never points the robot path at
`--repo`, so toolchains whose easyconfig isn't in the default robot index —
notably the newer `2025a` / `2025b` generations — raise
`Could not find easyconfig for foss toolchain version 2025a` and fall back to a
single-element chain `["foss-2025a"]`. The consequence:

```
compatible_toolchains("foss-2025a") == {"foss-2025a"}   # no sub-toolchains
```

Verified against the live graph: `tc-foss-2025a` exists as a `mc:Toolchain`
with `mc:toolchainId` but has **no** `mc:subToolchainOf` edge, whereas
`tc-gfbf-2024a` correctly links to `tc-gcc-13-3-0`.

Verified fix: configuring the robot path to the repo makes resolution succeed —
`foss-2025a → [GCCcore-14.2.0, GCC-14.2.0, gfbf-2025a, gompi-2025a, foss-2025a]`,
`foss-2025b → [GCCcore-14.3.0, …]`.

### Bug 2 — Dangling dependency edges (the main bug)

`parse_easyconfig` records each dependency via `d["full_mod_name"]` from
`ec.dependencies()`. Parsed in isolation (no robot resolution, no
minimal-toolchain mapping), EasyBuild stamps a dependency with the **parent**
easyconfig's toolchain, e.g.:

```
ABINIT/…-foss-2025a  mc:dependsOn  bzip2/1.0.8-foss-2025a   # stub — does not exist
```

The real module is `bzip2/1.0.8-GCCcore-14.x` — built with a **sub-toolchain**.

Measured on the live graph (`graphttl`, repo `../eb_gen/easybuild/easyconfigs`):

| metric | value |
|---|---|
| total `mc:dependsOn` edges | 33,295 |
| dangling edges (target not a real `mc:Module`) | 4,480 (~13%) |
| of those, resolvable by `(name, version)` to a real module | 3,901 (87%) |
| genuinely unresolvable (distinct full names) | 125 |

Because `solver._resolve_deps` recurses through `dependencies_of` →
`mc:dependsOn`, a dangling edge dead-ends the walk and drops the dependency's
own sub-dependencies from the recipe.

## Fix

> **Revision (2026-06-16, post-spike):** A targeted spike changed the approach.
> Both bugs share a single root cause — the indexer never points EasyBuild's
> robot search path at `--repo`. Setting it makes EB's *own* minimal-toolchain
> resolver return correct sub-toolchain module names directly, so the manual
> two-pass resolution post-pass, the `mc:versionSuffix` literal, and the static
> family-map fallback originally proposed here are **not needed**. We use EB's
> authoritative algorithm instead of reimplementing it. Verified:
>
> ```
> # igraph-0.10.17-foss-2025a, ec.dependencies() full_mod_name:
> no robot path:   zlib/1.3.1-foss-2025a      (dangling stub)
> robot = repo:    zlib/1.3.1-GCCcore-14.2.0   (real module)  ✓
> #                arpack-ng correctly stays foss-2025a
> ```

All changes are at **index time** (`indexer.py`) and require re-running
`modchef-index`. The fix relies on EasyBuild's authoritative resolver, not on
eb_gen's hardcoded family table or regex parser.

### Part A — Configure the robot path (the whole fix for both bugs)

In the indexer, configure EasyBuild's robot search path to `--repo` (the
directory being indexed) **before** any easyconfig parsing or toolchain-chain
resolution. Concretely:

1. Centralise EB configuration: a single `_ensure_configured(repo)` that calls
   `set_up_configuration(args=["--robot-paths=<repo>"], silent=True)`. Today
   both `indexer._ensure_configured` and `toolchains.resolve_chain` call
   `set_up_configuration` independently with no robot path; consolidate so the
   robot path is set once and consistently for both dependency parsing
   (`ec.dependencies()`) and toolchain-chain resolution
   (`get_toolchain_hierarchy`).
2. With the robot path set, `parse_easyconfig` keeps doing exactly what it does
   now (`deps.append(d["full_mod_name"])`) — the values are now correct
   sub-toolchain module names — and `_add_toolchain_hierarchy` now resolves
   full chains for newer toolchains (2025a/2025b), so `mc:subToolchainOf` edges
   are complete and `compatible_toolchains` is non-trivial.

No new schema fields. No resolution post-pass. No family-map fallback.

### Part B — Bundle-coverage salvage (modest; validate before keeping)

After Part A, **re-measure** the remaining dangling `mc:dependsOn` edges (the
4,480 figure was measured on the old, robot-path-less graph and will drop
sharply). For deps that *still* don't resolve to a standalone `mc:Module`, if
the dep's name matches a package some bundle module provides
(`mc:providesPackage` → `mc:Package` with that name) in a compatible toolchain,
emit a new `mc:coveredByBundle` edge from the depending module to the providing
bundle module.

This is a small, optional salvage layer. If the re-measured remainder shows it
salvages too few names to justify the code, drop it (YAGNI) and note the
decision in the plan.

## Measured results (2026-06-16, after implementation)

Re-indexing `../eb_gen/easybuild/easyconfigs` (11,062 files → 151,873 triples,
~80s) with the robot-path fix:

- **Dangling `mc:dependsOn` edges: 13.5% → 6.1%** (4,480 → 2,021 of 33,295;
  2,459 edges correctly retargeted to sub-toolchain modules).
- **Spot-check:** `igraph/0.10.17-foss-2025a` deps now resolve to
  `zlib/1.3.1-GCCcore-14.2.0`, `libxml2/2.13.4-GCCcore-14.2.0`,
  `GLPK/5.0-GCCcore-14.2.0` (all were dangling `-foss-2025a` before).
- **Hierarchy:** `foss-2025a/b`, `gfbf-2025b`, `intel-2025a` now carry full
  `mc:subToolchainOf` chains (were `{themselves}`).
- **Residual 6.1%:** dominated by deps EB resolved to correct module names that
  are not present in this repo (older/sibling builds), plus ~364/11,062 (3.3%)
  files the indexer's `except: continue` silently skips. The silent-skip is a
  pre-existing, separate issue — a candidate follow-up, out of scope here.

**Bundle-coverage decision: DROPPED (YAGNI).** Only ~10–13 genuinely
bundle-provided dep names were salvageable, and `mc:coveredByBundle` has no
consumer (the solver walks `mc:dependsOn`, not `coveredByBundle`) — the edges
would be inert metadata, the same reason easyblock nodes were excluded. Part B
is not implemented.

## Validation

- **Indexer regression test (fixtures):** building the test fixtures with the
  robot path set yields **0 dangling `mc:dependsOn` edges**, and the existing
  `test_parse_tool_extracts_dependency` (zlib → `GCCcore-12.3.0`) still passes.
- **Newer-toolchain hierarchy test:** a fixture/easyconfig on a `2025a`/`2025b`
  toolchain gains its full `mc:subToolchainOf` chain, so
  `compatible_toolchains("foss-2025a")` includes its GCCcore/GCC sub-toolchains
  (was `{"foss-2025a"}` before).
- **Real-repo check:** re-run
  `modchef-index --repo ../eb_gen/easybuild/easyconfigs` and confirm dangling
  edges drop sharply from 4,480 (target: down to genuinely-external deps only),
  and that `foss-2025a`/`foss-2025b` gain sub-toolchain chains.
- **Bundle-coverage decision:** record the re-measured unresolvable remainder
  and how many `mc:coveredByBundle` edges it produces; keep or drop Part B on
  that basis.
- **Solver behaviour:** a cooked recipe whose dependency previously dead-ended
  at a stub now includes the resolved module and its transitive deps.

## Out of scope (YAGNI)

modchef composes module-load recipes from existing modules; it does not author
easyconfigs. Therefore the following eb_gen features have no consumer here and
are intentionally excluded:

- Easyblock metadata nodes (`EasyBlock`, `implementedBy`).
- A separate transitive-dependency API — `solver._resolve_deps` already walks.
- eb_gen's regex `.eb` parser (modchef uses the authoritative EB framework
  parser) and its HTTP PyPI/CRAN enrichment.

## Open decisions deferred to implementation

- Whether bundle-coverage (Part B) survives the re-measured unresolvable
  remainder, or is dropped as YAGNI.
- Salvage predicate name is fixed as `mc:coveredByBundle` unless review prefers
  otherwise. (The `mc:versionSuffix` literal and static family-map fallback from
  the pre-spike draft are no longer part of the design.)
