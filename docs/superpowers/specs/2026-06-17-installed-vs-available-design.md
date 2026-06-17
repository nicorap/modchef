# Installed vs. Available Modules

## Problem

The HPC has not installed every EasyBuild easyconfig. Some modules are
custom-built locally; the bulk of the official EasyBuild collection is never
installed. Today the indexer treats every easyconfig in `ebfile_repo` as
cookable, so modchef has no way to tell a user "that tool exists in EasyBuild
but isn't installed here — ask support to install it."

We want two tiers of knowledge:

- **Installed** — modules actually built on the HPC (`ebfile_repo`). Cookable now.
- **Available** — easyconfigs in the official EasyBuild collection that are not
  installed. Known to exist, but must be requested from support before use.

`cook` should build environments from installed modules first, and when an
ingredient can only be satisfied by an available-but-not-installed module, warn
the user to ask support to install the specific easyconfig.

## Architecture

One RDF graph, two tiers, distinguished by a single boolean predicate
`mc:installed` on each `Module` node.

| Tier      | Source                  | Parse depth                                   | `mc:installed` |
|-----------|-------------------------|-----------------------------------------------|----------------|
| Installed | `ebfile_repo` (existing)| Full: deps, packages, toolchains (as today)   | `true`         |
| Available | `--official-repo` (new) | Full: deps, packages, toolchains (as today)   | `false`        |

> **Revision (2026-06-17):** the available tier was originally specced as a
> lightweight name/version/toolchain parse. It is now a **full** parse (same as
> installed), so not-installed bundles advertise their `exts_list` packages and
> their dependencies. This lets a Python/R package request resolve against a
> not-installed bundle ("ask support to install SciPy-bundle"), at the cost of a
> heavier daily index over the official collection. Both passes run in the same
> daily cron into one `.ttl`.

## Components

### `schema.py`
- Add predicate `MC.installed`.

### `indexer.py`
- `_add_facts(g, facts, installed=True)` emits
  `g.add((m, schema.MC.installed, Literal(installed)))`.
- The available pass reuses the existing full `parse_easyconfig`, so a
  not-installed module carries the same facts as an installed one (deps,
  `exts_list` packages, toolchain) — only `mc:installed` is `false`.
- `build_graph(..., official_paths=None, official_robot_paths=None)`: after the
  installed pass, the official pass points EasyBuild's robot path at the
  official repo (`official_robot_paths`) so the official easyconfigs' deps
  resolve against their own collection, then parses each path.
- `main()` gains an optional `--official-repo` argument. When provided, the
  official pass runs into the **same** output `.ttl` after the installed pass,
  with the official repo as its robot path.
- **Dedup rule:** the available pass skips any module whose `full_name` is
  already present from the installed pass, so a module that is both built and
  official remains a single `installed=true` node. Different *versions* of the
  same software coexist as separate nodes (one installed, one available).

### `graph.py`
- `ModuleRef` gains an `installed: bool` field.
- `_module_ref` reads `mc:installed` (default to `True` if the predicate is
  absent, for backward compatibility with older graphs).
- `modules_providing` / `search` are unchanged — they return both tiers; callers
  filter.

### `solver.py`
- `CookResult` gains `needs_install: list` of `(Ingredient, [ModuleRef])` where
  the `ModuleRef`s are the available candidates.
- In `cook()`'s candidate-lookup phase, per ingredient:
  - Partition candidates into installed and available (respecting
    `pinned_toolchain` for both).
  - If installed candidates exist → use **only those** for clustering. The
    existing greedy / toolchain-cluster logic is untouched.
  - Else if available candidates exist → append `(ing, available)` to
    `needs_install`.
  - Else → append to `unresolved` (as today).

### `output.py`
- After the module-load lines, `render()` emits a request-install section for
  each `needs_install` entry:
  ```
  # REQUEST INSTALL: samtools (tool) is available in EasyBuild but not installed on this HPC.
  #   ask support to install: SAMtools/1.22-GCC-14.3.0
  ```
  When multiple available candidates exist, list them on the `ask support`
  line. The partial recipe (all installed modules) is still emitted above.

### `cli.py`
- `_cmd_cook`: in addition to the rendered output, print an "ask support to
  install …" message to **stderr** for each `needs_install` entry. Exit
  **nonzero** when either `needs_install` or `unresolved` is non-empty (the
  environment is not fully cookable). The recipe for what *is* installed is
  still written/printed.
- `_cmd_search`: annotate each result as `full_name  [installed]` or
  `full_name  [available — not installed]`, with installed results sorted first.

`menu`, `explain`, `inspect`, and `ingredients` are unchanged.

## Data flow

```
indexer:  ebfile_repo   ─full parse─▶ Module(installed=true,  +deps +pkgs)
          official-repo ─full parse─▶ Module(installed=false, +deps +pkgs)   [skip if full_name already installed]
                                          │
                                          ▼
                                     modchef.ttl
                                          │
cook(ingredients):  per ingredient ──▶ installed candidates? ──yes──▶ cluster (existing logic)
                                          │no
                                          ▼
                                     available candidates? ──yes──▶ needs_install ("ask support")
                                          │no
                                          ▼
                                     unresolved
```

## Behavior notes

- **Packages and tools both span tiers.** Because the available tier is a full
  parse, a `--python` / `--r` package request resolves against not-installed
  bundles too: with no installed provider it lands in `needs_install` naming the
  bundle to request, not `unresolved`.
- **Version preference.** When a tool has both an installed older version and an
  available newer one, cook uses the installed one (installed candidates fully
  shadow available ones for that ingredient). This is intentional: prefer what
  the user can load now.

## Testing

- **indexer:** installed module marked `installed=true`; available marked
  `false`; dedup (module present in both repos → single `installed=true` node);
  a not-installed bundle's `exts_list` packages are indexed; a not-installed
  module's dependencies are indexed.
- **solver:** ingredient with only an available candidate → lands in
  `needs_install`, not `unresolved`; ingredient with an installed candidate →
  cooked, available copies ignored; ingredient absent from both tiers →
  `unresolved`.
- **output:** `render()` emits the request-install section for `needs_install`.
- **cli:** `search` annotates installed vs. available; `cook` prints the
  ask-support message to stderr and exits nonzero when `needs_install` is
  non-empty.

## Out of scope (YAGNI)

- Listing a requested module's transitive deps in the "ask support" message
  (EasyBuild resolves them at build time).
- Toolchain-aware install suggestions (e.g. "install a `foss-2025a` build of X
  to match the rest of the cluster").
- Availability annotations in `menu` / `explain` / `inspect`.
