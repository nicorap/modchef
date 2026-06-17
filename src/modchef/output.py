"""Render a CookResult into module-load recipes or sourceable scripts."""


def render(result, explain=False, as_script=False, name=None):
    lines = []
    if as_script:
        lines.append("#!/bin/bash")
        if name:
            lines.append(f"# modchef recipe: {name}")

    # Start from a clean environment so a copy-pasted (or sourced) recipe can't
    # collide with whatever the user already had loaded. Skip it when there is
    # nothing to load, so we don't wipe their environment for no reason.
    if any(cluster.modules for cluster in result.clusters):
        lines.append("module purge")

    multi = len(result.clusters) > 1
    for i, cluster in enumerate(result.clusters, start=1):
        if multi:
            tc = cluster.toolchain_id or "system"
            lines.append(f"# --- cluster {i}: {tc} ---")
        for mod in cluster.modules:
            line = f"module load {mod.full_name}"
            if explain and mod.full_name in cluster.reasons:
                reasons = "; ".join(cluster.reasons[mod.full_name])
                line += f"    # {reasons}"
            lines.append(line)

    u = result.unification
    if u is not None:
        names = sorted({ing.name for ing, _ in (u.installs + u.reused)})
        lines.append(
            f"# TO UNIFY these {len(result.clusters)} clusters under "
            f"{u.toolchain_id}, ask support to install:")
        for ing, mod in u.installs:
            lines.append(f"#   {mod.full_name}    ({ing.kind}: {ing.name})")
        lines.append(
            f"# then all of [{', '.join(names)}] load under {u.toolchain_id}.")

    for ing, mods in result.needs_install:
        names = ", ".join(sorted(m.full_name for m in mods))
        lines.append(
            f"# REQUEST INSTALL: {ing.name} ({ing.kind}) is available in "
            f"EasyBuild but not installed on this HPC.")
        lines.append(f"#   ask support to install: {names}")

    for ing in result.unresolved:
        lines.append(f"# WARNING: {ing.name} ({ing.kind}) not found in the catalog")

    return "\n".join(lines) + "\n"
