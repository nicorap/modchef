"""Cook solver: assign ingredients to the fewest compatible toolchain clusters."""
import re
from dataclasses import dataclass, field
from typing import Optional


def natural_key(value):
    """Split into int/str chunks so '1.10' > '1.9' and '2025a' > '2023b'."""
    parts = re.split(r"(\d+)", str(value))
    return [int(p) if p.isdigit() else p for p in parts]


@dataclass(frozen=True)
class Ingredient:
    kind: str            # "tool" | "python" | "r"
    name: str


@dataclass
class Cluster:
    toolchain_id: Optional[str]
    modules: list = field(default_factory=list)
    reasons: dict = field(default_factory=dict)


@dataclass
class CookResult:
    clusters: list = field(default_factory=list)
    unresolved: list = field(default_factory=list)
    needs_install: list = field(default_factory=list)


def _newest(modules):
    """Pick newest module by version using natural_key."""
    return max(modules, key=lambda m: natural_key(m.version))


def _candidates(graph, ingredient):
    """All modules that can supply an ingredient (may be empty)."""
    return graph.modules_providing(ingredient.name, kind=ingredient.kind)


def _resolve_deps(graph, module, seen):
    """Yield module then its transitive deps (deps first), deduped by full_name."""
    ordered = []
    def walk(m):
        for d in graph.dependencies_of(m.uri):
            if d.full_name not in seen:
                seen.add(d.full_name)
                walk(d)
                ordered.append(d)
    walk(module)
    if module.full_name not in seen:
        seen.add(module.full_name)
        ordered.append(module)
    return ordered


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


def _reason(ing):
    return f"requested {ing.kind}: {ing.name}"


def _expand_full(graph, chosen_for, cluster):
    """Emit every chosen module plus its full transitive deps (deps first)."""
    seen = set()
    for chosen, ing in chosen_for:
        cluster.reasons.setdefault(chosen.full_name, []).append(_reason(ing))
        for mod in _resolve_deps(graph, chosen, seen):
            if mod not in cluster.modules:
                cluster.modules.append(mod)


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
        reasons[chosen.full_name].append(_reason(ing))

    roots = [m for m in order
             if not any(m.full_name in closures[other.full_name]
                        for other in order if other.full_name != m.full_name)]

    cluster.modules = list(roots)
    cluster.reasons = {r.full_name: list(reasons[r.full_name]) for r in roots}

    # fold dropped modules' reasons into a root whose closure covers them.
    # If several roots cover the same dropped module, the first (in covered
    # order) receives it — the reason only needs to survive on one root.
    for m in order:
        if m in roots:
            continue
        for r in roots:
            if m.full_name in closures[r.full_name]:
                cluster.reasons[r.full_name].extend(reasons[m.full_name])
                break


def cook(graph, ingredients, pinned_toolchain=None, full=False):
    """Assign ingredients to the fewest compatible toolchain clusters."""
    # 1. candidate lookup
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

    remaining = list(cand.keys())
    clusters = []

    # 2/3. greedy: repeatedly choose the toolchain generation covering the most
    # still-unassigned ingredients (newest preferred on ties).
    while remaining:
        # candidate generation roots = every toolchain a candidate is built with
        roots = set()
        for ing in remaining:
            for m in cand[ing]:
                if m.toolchain_id:
                    roots.add(m.toolchain_id)
                else:
                    roots.add(None)

        def coverage(root):
            anc = graph.compatible_toolchains(root) if root else {None}
            covered = [ing for ing in remaining
                       if any((m.toolchain_id in anc) or
                              (m.toolchain_id is None and root is None)
                              for m in cand[ing])]
            return covered

        best_root = max(
            roots,
            key=lambda r: (len(coverage(r)),
                           natural_key(r) if r else natural_key("")))
        covered = coverage(best_root)
        anc = graph.compatible_toolchains(best_root) if best_root else {None}

        cluster = Cluster(toolchain_id=best_root)
        chosen_for = []
        for ing in covered:
            compatible_mods = [m for m in cand[ing]
                               if (m.toolchain_id in anc) or
                               (m.toolchain_id is None and best_root is None)]
            chosen_for.append((_newest(compatible_mods), ing))
        if full:
            _expand_full(graph, chosen_for, cluster)
        else:
            _minimize(graph, chosen_for, cluster)
        clusters.append(cluster)
        remaining = [ing for ing in remaining if ing not in covered]

    # rank clusters: largest first (best result = single cluster)
    clusters.sort(key=lambda c: -len(c.modules))
    return CookResult(clusters=clusters, unresolved=unresolved,
                      needs_install=needs_install)
