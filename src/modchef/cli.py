"""modchef command-line interface."""
import argparse
import os
import sys

from modchef import output, recipe, solver, suggest
from modchef.graph import ModChefGraph

DEFAULT_TTL = "/opt/easybuild/modchef/modchef.ttl"


def _candidate_names(graph, kind):
    """Names to suggest from, for a given ingredient kind."""
    if kind == "tool":
        return graph.all_software()
    return [name for name, eco in graph.all_packages() if eco == kind]


def _suggest(term, candidates, *, stream=None):
    """Print a 'did you mean' hint for `term` if any candidate is close."""
    if stream is None:
        stream = sys.stderr   # resolved at call time so capture/redirect works
    matches = suggest.closest(term, candidates)
    if matches:
        print(f"  did you mean: {', '.join(matches)}?", file=stream)


def _resolve_graph_path(arg):
    return arg or os.environ.get("MODCHEF_TTL") or DEFAULT_TTL


def _load_graph(path):
    path = _resolve_graph_path(path)
    if not os.path.exists(path):
        print(f"modchef: graph not found at {path}. Set $MODCHEF_TTL or run "
              f"modchef-index.", file=sys.stderr)
        return None
    return ModChefGraph.load(path)


def _cmd_cook(args):
    g = _load_graph(args.graph)
    if g is None:
        return 1
    if args.recipe_file:
        rec = recipe.load_recipe(args.recipe_file)
    else:
        rec = recipe.from_flags(name=args.name, tools=args.tools,
                                python=args.python, r=args.r)
    result = solver.cook(g, rec.ingredients, pinned_toolchain=args.toolchain,
                         full=args.full)
    as_script = bool(args.output)
    text = output.render(result, explain=args.explain, as_script=as_script,
                         name=args.name or rec.name)
    if args.dry_run:
        text = "# DRY RUN\n" + text
    if args.output and not args.dry_run:
        with open(args.output, "w") as fh:
            fh.write(text)
        print(f"modchef: wrote recipe to {args.output}")
    else:
        sys.stdout.write(text)
    # 'did you mean' hints go to stderr so stdout stays a clean recipe
    for ing in result.unresolved:
        print(f"modchef: {ing.name} ({ing.kind}) not found", file=sys.stderr)
        _suggest(ing.name, _candidate_names(g, ing.kind))
    for ing, mods in result.needs_install:
        names = ", ".join(sorted(m.full_name for m in mods))
        print(f"modchef: {ing.name} ({ing.kind}) is available in EasyBuild but "
              f"not installed; ask support to install: {names}", file=sys.stderr)
    return 1 if (result.unresolved or result.needs_install) else 0


def _cmd_search(args):
    g = _load_graph(args.graph)
    if g is None:
        return 1
    if args.python:
        term, kind, mods = args.python, "python", g.modules_providing(
            args.python, kind="python")
    elif args.r:
        term, kind, mods = args.r, "r", g.modules_providing(args.r, kind="r")
    else:
        term, kind, mods = args.term, "tool", g.search(args.term)
    if not mods:
        print("modchef: no matches")
        _suggest(term, _candidate_names(g, kind))
        return 1
    for m in sorted(mods, key=lambda x: (not x.installed, x.full_name)):
        tag = "installed" if m.installed else "available — not installed"
        print(f"{m.full_name}  [{tag}]")
    return 0


def _cmd_menu(args):
    g = _load_graph(args.graph)
    if g is None:
        return 1
    print("# Tools")
    for name in g.all_software():
        print(f"  {name}")
    print("# Packages")
    for name, eco in g.all_packages():
        print(f"  {name} ({eco})")
    return 0


def _cmd_inspect(args):
    g = _load_graph(args.graph)
    if g is None:
        return 1
    m = g.modules_by_full_name(args.module)
    if m is None:
        print(f"modchef: module not found: {args.module}", file=sys.stderr)
        _suggest(args.module, g.all_module_names())
        return 1
    print(f"{m.full_name}")
    print(f"  name:      {m.name}")
    print(f"  version:   {m.version}")
    print(f"  toolchain: {m.toolchain_id or 'system'}")
    deps = g.dependencies_of(m.uri)
    print("  depends on:")
    for d in deps:
        print(f"    {d.full_name}")
    return 0


def _cmd_ingredients(args):
    g = _load_graph(args.graph)
    if g is None:
        return 1
    if g.modules_by_full_name(args.module) is None:
        print(f"modchef: module not found: {args.module}", file=sys.stderr)
        _suggest(args.module, g.all_module_names())
        return 1
    pkgs = g.provided_packages(args.module)
    if not pkgs:
        print(f"modchef: {args.module} provides no extension packages")
        return 0
    for name, eco in pkgs:
        print(f"  {name} ({eco})")
    return 0


def _cmd_explain(args):
    g = _load_graph(args.graph)
    if g is None:
        return 1
    m = g.modules_by_full_name(args.module)
    if m is None:
        print(f"modchef: module not found: {args.module}", file=sys.stderr)
        _suggest(args.module, g.all_module_names())
        return 1
    print(f"{m.full_name} would be selected because:")
    print(f"  - it provides the software: {m.name}")
    print(f"  - it is built with toolchain: {m.toolchain_id or 'system'}")
    pkgs = g.provided_packages(m.full_name)
    if pkgs:
        print("  - it provides packages:")
        for name, eco in pkgs:
            print(f"      {name} ({eco})")
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="modchef",
        description="Cook EasyBuild environments from software ingredients.")
    sub = parser.add_subparsers(dest="command", required=True)

    cook = sub.add_parser("cook", help="Generate a module-load recipe.")
    cook.add_argument("recipe_file", nargs="?", help="recipe.yaml file.")
    cook.add_argument("--graph", help="Path to modchef .ttl "
                      "(default: $MODCHEF_TTL).")
    cook.add_argument("--tools", nargs="*", default=[])
    cook.add_argument("--python", nargs="*", default=[])
    cook.add_argument("--r", nargs="*", default=[])
    cook.add_argument("--name")
    cook.add_argument("--toolchain")
    cook.add_argument("--output")
    cook.add_argument("--explain", action="store_true")
    cook.add_argument("--dry-run", action="store_true")
    cook.add_argument("--full", action="store_true",
                      help="Emit the complete transitive dependency list "
                           "instead of only the modules Lmod won't auto-load.")
    cook.set_defaults(func=_cmd_cook)

    search = sub.add_parser("search", help="Find matching modules/packages.")
    search.add_argument("term", nargs="?")
    search.add_argument("--python")
    search.add_argument("--r")
    search.add_argument("--graph", help="Path to modchef .ttl "
                        "(default: $MODCHEF_TTL).")
    search.set_defaults(func=_cmd_search)

    menu = sub.add_parser("menu", help="List known tools and packages.")
    menu.add_argument("--graph", help="Path to modchef .ttl "
                      "(default: $MODCHEF_TTL).")
    menu.set_defaults(func=_cmd_menu)

    inspect = sub.add_parser("inspect", help="Show raw facts about a module.")
    inspect.add_argument("module")
    inspect.add_argument("--graph", help="Path to modchef .ttl "
                         "(default: $MODCHEF_TTL).")
    inspect.set_defaults(func=_cmd_inspect)

    ingredients = sub.add_parser(
        "ingredients", help="List packages a bundle provides.")
    ingredients.add_argument("module")
    ingredients.add_argument("--graph", help="Path to modchef .ttl "
                             "(default: $MODCHEF_TTL).")
    ingredients.set_defaults(func=_cmd_ingredients)

    explain = sub.add_parser("explain", help="Explain a module's provision.")
    explain.add_argument("module")
    explain.add_argument("--graph", help="Path to modchef .ttl "
                         "(default: $MODCHEF_TTL).")
    explain.set_defaults(func=_cmd_explain)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
