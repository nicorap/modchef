#!/usr/bin/env python3
"""Find shared unbuilt dependencies from a modchef graph.

Replaces the per-recipe `eb --dry-run` sweep, which cost ~2s per recipe (200s+
for a tier and still incomplete inside a 10-minute cron cycle). One graph
traversal answers the same question in milliseconds.

VALIDATION TARGET: the X11 incident of 2026-09-27. ImageMagick, FFmpeg,
Gdk-Pixbuf and HarfBuzz each ran `eb --robot`, each began building X11 (~40
components, ~1.5h), and three then blocked on its lock holding a node each. If
this script is right, X11 shows up as the top shared dependency of exactly those
consumers.

Installed-ness is read from the MODULEFILE TREE, not from the graph: the graph
was built from easyconfigs, which say what a recipe needs, not what exists. A
recipe counts as built only when a modulefile exists for that exact version --
an install directory proves nothing, since EasyBuild creates it before
populating it.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path


def strip_class(full_name: str) -> str:
    """Drop EasyBuild's module-class prefix, if present.

    EasyBuild's `full_mod_name` is CLASS/PKG/VERSION when a module naming scheme
    with class dirs is in force (e.g. 'vis/X11/20250608-GCCcore-14.3.0'), but a
    recipe's own full_name is PKG/VERSION. So dependency edges point at
    class-prefixed URIs while the recipes themselves are keyed without the class
    -- which is why every shared dep reported NO PATH: the lookup missed.
    Normalising to PKG/VERSION joins the two halves of the graph.
    """
    parts = full_name.split("/")
    if len(parts) >= 3:
        return "/".join(parts[-2:])
    return full_name


def module_exists(modules_root: Path, full_name: str) -> bool:
    """full_name is 'X11/20250608-GCCcore-14.3.0'; the tree adds a class dir."""
    full_name = strip_class(full_name)
    if "/" not in full_name:
        return False
    pkg, ver = full_name.split("/", 1)
    for cand in modules_root.glob(f"*/{pkg}/{ver}.lua"):
        if cand.exists():
            return True
    for cand in modules_root.glob(f"*/{pkg}/{ver}"):
        if cand.is_file():
            return True
    # Lmod version aliases (.modulerc.lua) can satisfy a request without a file
    # of that name -- this is how Java caps looked like false failures.
    for rc in modules_root.glob(f"*/{pkg}/.modulerc.lua"):
        try:
            if ver in rc.read_text(errors="replace"):
                return True
        except OSError:
            pass
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--graph", required=True)
    ap.add_argument("--modules", required=True)
    ap.add_argument("--tier", help="restrict consumers to this tier list")
    ap.add_argument("--min-consumers", type=int, default=2)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--modchef-src",
                    help="path to a modchef source tree (src/), when modchef is "
                         "not installed in the interpreter running this script")
    a = ap.parse_args()

    # modchef is normally installed (pip install modchef); --modchef-src is only
    # for running against a source checkout without installing it.
    if a.modchef_src:
        sys.path.insert(0, a.modchef_src)
    from modchef.graph import ModChefGraph

    g = ModChefGraph.load(a.graph)
    modules_root = Path(a.modules)

    wanted: set[str] | None = None
    if a.tier:
        wanted = set()
        for line in Path(a.tier).read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                wanted.add(line[:-3] if line.endswith(".eb") else line)

    # Candidate consumers: every module in the graph that is NOT yet built.
    consumers: dict[str, set[str]] = defaultdict(set)
    paths: dict[str, str] = {}
    pending: list[str] = []

    for full in g.all_module_names():
        ref = g.modules_by_full_name(full)
        if ref is None or ref.easyconfig_path is None:
            continue  # dependency stub, not a recipe we hold
        if wanted is not None:
            stem = Path(ref.easyconfig_path).name[:-3]
            if stem not in wanted:
                continue
        if module_exists(modules_root, full):
            continue
        pending.append(full)
        paths[full] = ref.easyconfig_path

    for full in pending:
        ref = g.modules_by_full_name(full)
        for dep_name, dep_ref in g.build_closure(ref.uri, include_installed=True).items():
            key = strip_class(dep_name)
            if module_exists(modules_root, key):
                continue
            if key == full:
                continue
            consumers[key].add(full)
            # The dep edge may point at a class-prefixed stub with no facts;
            # the real recipe is keyed without the class.
            if dep_ref.easyconfig_path:
                paths.setdefault(key, dep_ref.easyconfig_path)
            else:
                real = g.modules_by_full_name(key)
                if real is not None and real.easyconfig_path:
                    paths.setdefault(key, real.easyconfig_path)

    shared = sorted(
        ((d, c) for d, c in consumers.items() if len(c) >= a.min_consumers),
        key=lambda kv: -len(kv[1]),
    )

    print(f"pending recipes: {len(pending)}")
    print(f"unbuilt deps with >= {a.min_consumers} consumers: {len(shared)}")
    print()
    print(f"{'consumers':>9}  {'dependency':50}  submittable")
    print("-" * 78)
    for dep, cs in shared[: a.top]:
        p = paths.get(dep)
        print(f"{len(cs):>9}  {dep:50}  {'yes' if p else 'NO PATH'}")

    if shared:
        top, cs = shared[0]
        print()
        print(f"top shared dep: {top}")
        print(f"  easyconfig: {paths.get(top, '(unknown)')}")
        print(f"  consumers ({len(cs)}):")
        for c in sorted(cs)[:12]:
            print(f"    {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
