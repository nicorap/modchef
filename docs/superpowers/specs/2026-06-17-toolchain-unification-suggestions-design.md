# Toolchain Unification Suggestions

## Problem

When a user requests several tools, modchef assigns them to the fewest
compatible toolchain clusters. Sometimes the *installed* builds of those tools
share no compatible toolchain, so cook emits more than one cluster:

```
$ modchef cook --tools toola toolb
# --- cluster 1: foss-2022a ---
module load ToolA/1.2-foss-2022a
# --- cluster 2: foss-2024a ---
module load ToolB/3.4-foss-2024a
```

The user wants them in one environment. Often the official EasyBuild collection
(the *available, not-installed* tier) contains a build of one tool on a
toolchain compatible with the other — so support could install it and unify the
set. modchef should detect this and suggest the install.

This spec covers **generic toolchain unification**. Conda-based modules (which
bundle their own Python and conflict with EasyBuild Python modules) are a
separate, related problem handled by a later "Conda-awareness" feature.

## Scope

- **In scope:** when installed-only clustering yields more than one cluster,
  search the available tier for a single toolchain generation that covers all
  the clustered ingredients (installed or available), and suggest the installs
  needed to reach it.
- **Out of scope (deferred):** Conda-awareness; partial unification (reducing
  from N clusters to fewer-but->1); toolchain-aware refinement of the existing
  per-ingredient `needs_install` suggestions.

## Architecture

Unification is computed inside `cook()` after the existing installed-only
clustering, and is **orthogonal to `needs_install`**:

- `needs_install` (unchanged): an ingredient with no installed build anywhere
  but an available one. These are never part of clusters.
- `unification` (new): concerns only ingredients that *are* installed-resolvable
  (already placed in clusters) but spread across more than one cluster. It
  suggests installing compatible builds so those clusters merge into one.

The two never overlap: `needs_install` ingredients are excluded from the
unification search.

### Trigger

Run the unification search only when the installed-only clustering produced
`len(clusters) > 1`. With a single cluster there is nothing to unify.

### Algorithm

Let `S` be the set of requested ingredients currently placed in clusters (the
keys of the installed-candidate map). For each candidate toolchain generation
`G` — drawn from the toolchain ids of every candidate (installed **and**
available) of the ingredients in `S`:

1. `G` is a **unifying generation** if every ingredient in `S` has at least one
   candidate (installed or available) whose toolchain is compatible with `G`,
   i.e. `m.toolchain_id in graph.compatible_toolchains(G)` (the existing
   ancestor closure).
2. For a unifying `G`, the **installs needed** are the ingredients in `S` that
   have no *installed* G-compatible candidate but do have an available one; pick
   the newest available G-compatible module per such ingredient
   (`_newest` by version).
3. The **reused** modules are, for the remaining ingredients in `S`, the newest
   installed G-compatible module.

Among all unifying generations, choose the one with the **fewest installs**,
breaking ties by **newest** generation (`natural_key`). If no unifying `G`
exists, emit no suggestion; the multi-cluster recipe stands.

The search is bounded: O(generations × |S| × candidates), reusing
`compatible_toolchains` and `natural_key` already in the solver.

## Components

### `solver.py`

- New dataclass:
  ```python
  @dataclass
  class Unification:
      toolchain_id: str
      installs: list = field(default_factory=list)  # [(Ingredient, ModuleRef)]
      reused: list = field(default_factory=list)     # [(Ingredient, ModuleRef)]
  ```
- `CookResult` gains `unification: Optional[Unification] = None`.
- `cook()` retains the full candidate map (installed + available) per ingredient
  so the search can see available builds. After clustering, if
  `len(clusters) > 1`, run `_unify(graph, clustered_candidates)` and set
  `result.unification` when a unifying generation is found.
- `_unify` returns a `Unification` or `None`. The `ModuleRef`s in `installs` are
  available (`installed == False`); those in `reused` are installed.

### `output.py`

The multi-cluster recipe renders unchanged. When `result.unification` is set,
append a block after the cluster load lines:

```
# TO UNIFY these 2 clusters under foss-2024a, ask support to install:
#   ToolA/1.2-foss-2024a    (tool: toola)
# then all of [toola, toolb] load under foss-2024a.
```

The count ("2 clusters") is `len(result.clusters)`; the bracketed list is the
ingredient names in `S`; one `#   <full_name>    (<kind>: <name>)` line per
entry in `installs`.

### `cli.py`

`_cmd_cook` prints a one-line suggestion to **stderr** (alongside the existing
`needs_install` messages):

```
modchef: to unify under foss-2024a, ask support to install: ToolA/1.2-foss-2024a
```

**Exit code is unchanged.** A unification suggestion is informational — the
emitted multi-cluster recipe is fully loadable — so it does not by itself cause
a nonzero exit. cook still exits nonzero only for `unresolved` or `needs_install`
(as today).

## Data flow

```
cook(ingredients):
  per ingredient -> full candidates (installed + available)
  installed-only clustering -> clusters (working recipe, unchanged)
        |
        | len(clusters) > 1 ?
        v yes
  _unify over clustered ingredients' full candidates:
     for each generation G covering all of them (installed|available):
        installs = ingredients with no installed G-compatible build (use newest available)
     pick fewest installs, then newest G
        |
        v found
  result.unification = Unification(G, installs, reused)
```

## Testing

- **solver:**
  - Two tools whose installed builds split across toolchains, with an available
    build of one on the other's toolchain → `unification` set to that generation
    with the expected single install; `reused` holds the already-installed one.
  - Tie-break: two unifying generations needing equal installs → the newer one
    is chosen; a generation needing fewer installs beats a newer one needing
    more.
  - Single installed cluster → `unification is None`.
  - Split with no generation able to cover everything (even via available) →
    `unification is None`.
  - `needs_install` ingredients are excluded from the unification search.
- **output:** with a `Unification` set, the TO-UNIFY block renders with the
  target toolchain and one line per install; absent when `unification is None`.
- **cli:** the stderr "to unify … ask support to install …" line appears; exit
  code stays 0 when the multi-cluster recipe is otherwise complete (no
  `unresolved`/`needs_install`).

## Fixtures

Extend `tests/fixtures/sample.ttl` with a minimal split-then-unify case:
- `ToolX` installed only on an older generation and available on a newer one.
- `ToolY` installed only on that newer generation.
- Requesting `toolx tooly` splits installed-only, and unifies under the newer
  generation by installing the available `ToolX`.
Use the existing `gfbf`/`GCC` toolchain nodes (and add one more generation if
needed) so `compatible_toolchains` resolves the ancestor closure.
