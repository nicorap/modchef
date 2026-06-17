"""Build-time indexer: parse EasyBuild easyconfigs into an RDF graph.

Requires easybuild-framework + easybuild-easyblocks (the cron runs as the EasyBuild admin user
on the HPC, where both are installed).
"""
import argparse
import glob
import os
import sys
from dataclasses import dataclass, field

from rdflib import Graph, Literal, RDF
from modchef import schema, toolchains

_CONFIGURED = False


def configure_easybuild(robot_paths=None):
    """Configure EasyBuild, pointing the robot search path at `robot_paths`.

    The robot path is what lets EB's minimal-toolchain resolver map a dependency
    like ('zlib', '1.3.1') under a foss-2025a recipe to the real sub-toolchain
    module zlib/1.3.1-GCCcore-14.2.0. EB configuration is a global singleton, so
    reconfigure=True is required for the robot path to take effect even if an
    earlier call already configured EB.
    """
    global _CONFIGURED
    from easybuild.tools.options import set_up_configuration
    args = [f"--robot-paths={robot_paths}"] if robot_paths else []
    set_up_configuration(args=args, silent=True, reconfigure=True)
    _CONFIGURED = True


def _ensure_configured():
    # Lazy gate for direct parse_easyconfig() callers. `_CONFIGURED` means "EB
    # has been configured" — once build_graph()/main() has called
    # configure_easybuild(repo) with the authoritative robot path, this becomes
    # a no-op and must NOT reconfigure EB back to the default (path-less) setup.
    if not _CONFIGURED:
        configure_easybuild()


def _ecosystem_for(name, easyblock):
    """Classify extension packages as 'python' or 'r' (None if unknown)."""
    eb = (easyblock or "").lower()
    nm = (name or "").lower()
    if "python" in eb or nm in ("python", "scipy-bundle"):
        return "python"
    if eb.startswith("r") or nm in ("r", "r-bundle-cran", "r-bundle-bioconductor"):
        return "r"
    return None


@dataclass
class ModuleFacts:
    name: str
    version: str
    versionsuffix: str
    toolchain_name: str
    toolchain_version: str
    moduleclass: str
    full_name: str
    dependencies: list = field(default_factory=list)   # [full_mod_name, ...]
    packages: list = field(default_factory=list)       # [(pkg_name, ecosystem)]


def parse_easyconfig(path) -> ModuleFacts:
    _ensure_configured()
    from easybuild.framework.easyconfig.easyconfig import EasyConfig
    ec = EasyConfig(path)
    tc = ec["toolchain"]
    name, version = ec["name"], ec["version"]
    versionsuffix = ec["versionsuffix"] or ""
    full_name = schema.full_module_name(
        name, version, tc["name"], tc["version"], versionsuffix)

    deps = []
    for d in ec.dependencies():
        if d.get("external_module"):
            continue
        if d.get("build_only"):
            continue   # build deps are not part of a runtime recipe
        deps.append(d["full_mod_name"])

    ecosystem = _ecosystem_for(name, ec["easyblock"])
    packages = []
    for ext in ec["exts_list"]:
        ext_name = ext if isinstance(ext, str) else ext[0]
        if isinstance(ext_name, str) and ecosystem:
            packages.append((ext_name, ecosystem))

    return ModuleFacts(
        name=name, version=version, versionsuffix=versionsuffix,
        toolchain_name=tc["name"], toolchain_version=tc["version"],
        moduleclass=ec["moduleclass"] or "", full_name=full_name,
        dependencies=deps, packages=packages,
    )


def _add_facts(g, facts, installed=True):
    m = schema.module_uri(facts.full_name)
    g.add((m, RDF.type, schema.MC.Module))
    g.add((m, schema.MC.installed, Literal(installed)))
    g.add((m, schema.MC.name, Literal(facts.name)))
    g.add((m, schema.MC.version, Literal(facts.version)))
    g.add((m, schema.MC.fullName, Literal(facts.full_name)))
    if facts.moduleclass:
        g.add((m, schema.MC.moduleClass, Literal(facts.moduleclass)))

    sw = schema.software_uri(facts.name)
    g.add((sw, RDF.type, schema.MC.Software))
    g.add((sw, schema.MC.name, Literal(facts.name.lower())))
    g.add((m, schema.MC.providesSoftware, sw))

    tc_id = schema.toolchain_id(facts.toolchain_name, facts.toolchain_version)
    if tc_id:
        t = schema.toolchain_uri(tc_id)
        g.add((t, RDF.type, schema.MC.Toolchain))
        g.add((t, schema.MC.name, Literal(facts.toolchain_name)))
        g.add((t, schema.MC.version, Literal(facts.toolchain_version)))
        g.add((t, schema.MC.toolchainId, Literal(tc_id)))
        g.add((m, schema.MC.builtWith, t))

    for dep_full in facts.dependencies:
        d = schema.module_uri(dep_full)
        # If the dependency is itself indexed, this URI already carries its full
        # facts; otherwise we record at least its fullName so recipes can load it.
        g.add((d, schema.MC.fullName, Literal(dep_full)))
        g.add((m, schema.MC.dependsOn, d))

    for pkg_name, ecosystem in facts.packages:
        p = schema.package_uri(pkg_name, ecosystem)
        g.add((p, RDF.type, schema.MC.Package))
        g.add((p, schema.MC.name, Literal(pkg_name.lower())))
        g.add((p, schema.MC.ecosystem, Literal(ecosystem)))
        g.add((m, schema.MC.providesPackage, p))


def _add_toolchain_hierarchy(g):
    """Emit generation-aware mc:subToolchainOf edges for every toolchain node.

    For each Toolchain in the graph, resolve its base-first chain (e.g.
    gfbf-2023a -> [GCCcore-12.3.0, GCC-12.3.0, gfbf-2023a]) and link each level
    to the one below it: c[i+1] subToolchainOf c[i]. Ancestor toolchains are
    materialised as Toolchain nodes too, so the hierarchy is complete even if a
    given sub-toolchain has no module of its own.
    """
    seen_tcs = set()
    for t in set(g.subjects(RDF.type, schema.MC.Toolchain)):
        name = g.value(t, schema.MC.name)
        version = g.value(t, schema.MC.version)
        if name is None or version is None:
            continue
        seen_tcs.add((str(name), str(version)))

    def tc_node(tc_id):
        # tc_id is authoritative (stored as mc:toolchainId). name/version are
        # best-effort for display; the runtime keys off mc:toolchainId only.
        nm, _, ver = tc_id.rpartition("-")
        node = schema.toolchain_uri(tc_id)
        g.add((node, RDF.type, schema.MC.Toolchain))
        g.add((node, schema.MC.name, Literal(nm)))
        g.add((node, schema.MC.version, Literal(ver)))
        g.add((node, schema.MC.toolchainId, Literal(tc_id)))
        return node

    for name, version in seen_tcs:
        chain = toolchains.resolve_chain(name, version)
        for lower_id, upper_id in zip(chain, chain[1:]):
            upper = tc_node(upper_id)
            lower = tc_node(lower_id)
            g.add((upper, schema.MC.subToolchainOf, lower))


def build_graph(paths, with_toolchain_hierarchy=False, robot_paths=None,
                official_paths=None, official_robot_paths=None):
    if robot_paths:
        configure_easybuild(robot_paths)
    g = Graph()
    g.bind("mc", schema.MC)
    for path in paths:
        try:
            _add_facts(g, parse_easyconfig(path))
        except Exception:
            continue   # resilient: skip unparseable configs (logged in main)
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
            except Exception:
                continue   # resilient: skip unparseable configs
            if facts.full_name in installed_names:
                continue   # already installed; do not downgrade to available
            _add_facts(g, facts, installed=False)
    if with_toolchain_hierarchy:
        _add_toolchain_hierarchy(g)
    return g


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="modchef-index",
        description="Parse EasyBuild easyconfigs into a modchef RDF graph.")
    parser.add_argument("--repo", default="/opt/easybuild/ebfiles_repo",
                        help="Root directory containing *.eb files.")
    parser.add_argument("--output", required=True, help="Output .ttl path.")
    parser.add_argument("--glob", default="**/*.eb",
                        help="Glob (relative to --repo) for easyconfigs.")
    parser.add_argument("--official-repo", default=None,
                        help="Root of the official EasyBuild easyconfig "
                             "collection (parsed lightweight as 'available').")
    args = parser.parse_args(argv)

    paths = glob.glob(os.path.join(args.repo, args.glob), recursive=True)
    if not paths:
        print(f"modchef-index: no easyconfigs under {args.repo}", file=sys.stderr)
        return 1
    official = None
    official_root = None
    if args.official_repo:
        official = glob.glob(
            os.path.join(args.official_repo, args.glob), recursive=True)
        official_root = os.path.abspath(args.official_repo)
    g = build_graph(paths, with_toolchain_hierarchy=True,
                    robot_paths=os.path.abspath(args.repo),
                    official_paths=official,
                    official_robot_paths=official_root)
    g.serialize(args.output, format="turtle")
    print(f"modchef-index: wrote {len(g)} triples from {len(paths)} files "
          f"to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
