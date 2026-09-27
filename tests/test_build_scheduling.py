"""Build-scheduling queries: build deps, easyconfig paths, build closure.

These cover the distinction that makes the feature safe: a BUILD dependency must
be visible to a build scheduler and invisible to a runtime `module load` plan.
"""


def test_build_dependencies_are_separate_from_runtime(sample_graph):
    c1 = sample_graph.modules_by_full_name("ConsumerOne/1.0-GCCcore-12.3.0")
    runtime = {d.full_name for d in sample_graph.dependencies_of(c1.uri)}
    build = {d.full_name for d in sample_graph.build_dependencies_of(c1.uri)}

    # The build dep must NOT appear as a runtime dep -- this is what keeps
    # `modchef cook` emitting loadable-at-runtime modules only.
    assert "BuildTool/2.0-GCCcore-12.3.0" in build
    assert "BuildTool/2.0-GCCcore-12.3.0" not in runtime
    assert "zlib/1.2.13-GCCcore-12.3.0" in runtime


def test_all_dependencies_unions_both_kinds(sample_graph):
    c1 = sample_graph.modules_by_full_name("ConsumerOne/1.0-GCCcore-12.3.0")
    both = {d.full_name for d in sample_graph.all_dependencies_of(c1.uri)}
    assert "BuildTool/2.0-GCCcore-12.3.0" in both
    assert "zlib/1.2.13-GCCcore-12.3.0" in both


def test_all_dependencies_dedups_by_full_name(sample_graph):
    c1 = sample_graph.modules_by_full_name("ConsumerOne/1.0-GCCcore-12.3.0")
    names = [d.full_name for d in sample_graph.all_dependencies_of(c1.uri)]
    assert len(names) == len(set(names))


def test_easyconfig_path_is_exposed(sample_graph):
    c1 = sample_graph.modules_by_full_name("ConsumerOne/1.0-GCCcore-12.3.0")
    assert c1.easyconfig_path == (
        "/site/easyconfigs/c/ConsumerOne/ConsumerOne-1.0-GCCcore-12.3.0.eb"
    )


def test_easyconfig_path_absent_is_none(sample_graph):
    # Recipes indexed before this feature carry no mc:easyconfigPath.
    sam = next(m for m in sample_graph.modules_providing("samtools", kind="tool")
               if m.name == "SAMtools")
    assert sam.easyconfig_path is None


def test_build_closure_reports_only_uninstalled_by_default(sample_graph):
    c1 = sample_graph.modules_by_full_name("ConsumerOne/1.0-GCCcore-12.3.0")
    closure = sample_graph.build_closure(c1.uri)
    # BuildTool is installed=false -> must be reported as work to do.
    assert "BuildTool/2.0-GCCcore-12.3.0" in closure
    # zlib is installed -> not work.
    assert "zlib/1.2.13-GCCcore-12.3.0" not in closure


def test_build_closure_can_include_installed(sample_graph):
    c1 = sample_graph.modules_by_full_name("ConsumerOne/1.0-GCCcore-12.3.0")
    closure = sample_graph.build_closure(c1.uri, include_installed=True)
    assert "zlib/1.2.13-GCCcore-12.3.0" in closure


def test_shared_build_dep_is_detectable_across_consumers(sample_graph):
    """The X11 case: one unbuilt dep wanted by several pending recipes.

    Counting consumers is the whole point -- a dep with 2+ pending consumers
    should be built ONCE, first, instead of each consumer's `eb --robot`
    discovering it concurrently and serialising on its lock.
    """
    pending = [
        sample_graph.modules_by_full_name("ConsumerOne/1.0-GCCcore-12.3.0"),
        sample_graph.modules_by_full_name("ConsumerTwo/1.0-GCCcore-12.3.0"),
    ]
    consumers = {}
    for m in pending:
        for name in sample_graph.build_closure(m.uri):
            consumers.setdefault(name, set()).add(m.full_name)

    shared = {n: c for n, c in consumers.items() if len(c) >= 2}
    assert "BuildTool/2.0-GCCcore-12.3.0" in shared
    assert len(shared["BuildTool/2.0-GCCcore-12.3.0"]) == 2

    # And it must be submittable: the scheduler needs a real .eb path.
    bt = sample_graph.modules_by_full_name("BuildTool/2.0-GCCcore-12.3.0")
    assert bt.easyconfig_path.endswith("BuildTool-2.0-GCCcore-12.3.0.eb")


def test_build_closure_terminates_on_cycle(sample_graph):
    """A malformed recipe pair must not hang a cron job."""
    from rdflib import Literal, URIRef
    from modchef import schema

    a = schema.module_uri("CycleA/1.0")
    b = schema.module_uri("CycleB/1.0")
    for u, full in ((a, "CycleA/1.0"), (b, "CycleB/1.0")):
        sample_graph.g.add((u, schema.MC.fullName, Literal(full)))
        sample_graph.g.add((u, schema.MC.installed, Literal(False)))
    sample_graph.g.add((a, schema.MC.buildDependsOn, b))
    sample_graph.g.add((b, schema.MC.buildDependsOn, a))

    closure = sample_graph.build_closure(str(a))
    assert "CycleB/1.0" in closure
