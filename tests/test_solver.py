from modchef import solver

def test_natural_key_orders_versions():
    versions = ["1.9", "1.10", "1.2"]
    assert sorted(versions, key=solver.natural_key) == ["1.2", "1.9", "1.10"]

def test_natural_key_orders_toolchain_generations():
    gens = ["2023b", "2025a", "2023a"]
    assert sorted(gens, key=solver.natural_key) == ["2023a", "2023b", "2025a"]


def _ingredients():
    return [solver.Ingredient("tool", "samtools"),
            solver.Ingredient("python", "pandas")]


def test_single_cluster_when_compatible(sample_graph):
    # SAMtools is GCC-12.3.0; SciPy-bundle is gfbf-2023a. GCC-12.3.0 is the
    # compiler of the 2023a generation, so gfbf-2023a covers both -> 1 cluster.
    result = solver.cook(sample_graph, _ingredients())
    assert result.unresolved == []
    assert len(result.clusters) == 1
    chosen = {m.name for c in result.clusters for m in c.modules}
    assert "SAMtools" in chosen
    assert "SciPy-bundle" in chosen


def test_available_only_goes_to_needs_install(sample_graph):
    res = solver.cook(sample_graph, [solver.Ingredient("tool", "star")])
    assert res.clusters == []
    names = [ing.name for ing, mods in res.needs_install]
    assert "star" in names
    assert solver.Ingredient("tool", "star") not in res.unresolved

def test_available_package_goes_to_needs_install(sample_graph):
    # a Python package provided only by a not-installed bundle (and by no
    # standalone module) is a request, not unresolved.
    res = solver.cook(sample_graph, [solver.Ingredient("python", "labpipe")])
    assert res.clusters == []
    names = [ing.name for ing, mods in res.needs_install]
    assert "labpipe" in names
    assert solver.Ingredient("python", "labpipe") not in res.unresolved

def test_package_resolves_to_standalone_module(sample_graph):
    # biopython is installed as its own module (provides software 'biopython');
    # a --python biopython request must load it, not suggest the Biotools bundle
    # that merely lists biopython in its exts_list.
    res = solver.cook(sample_graph, [solver.Ingredient("python", "biopython")])
    assert res.needs_install == []
    loaded = [m.full_name for c in res.clusters for m in c.modules]
    assert "Biopython/1.85-gfbf-2023a" in loaded
    assert "Biotools/1.0-gfbf-2023a" not in loaded

def test_installed_shadows_available(sample_graph):
    res = solver.cook(sample_graph, [solver.Ingredient("tool", "gatk")])
    assert res.needs_install == []
    loaded = [m.full_name for c in res.clusters for m in c.modules]
    assert "GATK/4.5.0.0-GCC-12.3.0" in loaded
    assert "GATK/4.6.0.0-GCC-12.3.0" not in loaded

def test_missing_everywhere_still_unresolved(sample_graph):
    res = solver.cook(sample_graph, [solver.Ingredient("tool", "nope")])
    assert solver.Ingredient("tool", "nope") in res.unresolved
    assert res.needs_install == []

def test_unresolved_ingredient_reported(sample_graph):
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "does-not-exist")])
    assert solver.Ingredient("tool", "does-not-exist") in result.unresolved


def test_two_gcc_tools_form_one_cluster(sample_graph):
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools"),
                          solver.Ingredient("tool", "bcftools")])
    assert result.unresolved == []
    assert len(result.clusters) == 1
    names = {m.name for m in result.clusters[0].modules}
    assert names == {"SAMtools", "BCFtools"}  # zlib auto-loaded, not emitted


def test_minimal_drops_transitive_dep(sample_graph):
    # SAMtools depends on zlib; minimal output must NOT emit zlib.
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools")])
    names = [m.name for c in result.clusters for m in c.modules]
    assert names == ["SAMtools"]
    assert "zlib" not in names


def test_minimal_collapses_requested_dep(sample_graph):
    # Requesting both samtools and zlib: zlib is a runtime dep of SAMtools,
    # so the minimal set is just SAMtools, and its reasons carry both requests.
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools"),
                          solver.Ingredient("tool", "zlib")])
    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert [m.name for m in cluster.modules] == ["SAMtools"]
    reasons = cluster.reasons["SAMtools/1.18-GCC-12.3.0"]
    assert "requested tool: samtools" in reasons
    assert "requested tool: zlib" in reasons


def test_minimal_folds_transitive_chain(sample_graph):
    # ToolA -> ToolB -> ToolC. Requesting all three collapses to the single
    # root ToolA, and reasons for the dropped links fold onto it even though
    # ToolC is only reachable from ToolA transitively (via ToolB).
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "toola"),
                          solver.Ingredient("tool", "toolb"),
                          solver.Ingredient("tool", "toolc")])
    assert len(result.clusters) == 1
    cluster = result.clusters[0]
    assert [m.name for m in cluster.modules] == ["ToolA"]
    reasons = cluster.reasons["ToolA/1.0-GCC-12.3.0"]
    assert "requested tool: toola" in reasons
    assert "requested tool: toolb" in reasons
    assert "requested tool: toolc" in reasons


def test_incompatible_generations_fall_back_to_two_clusters(sample_graph):
    # SAMtools is GCC-12.3.0; BWA is GCC-13.2.0 -> no common generation.
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools"),
                          solver.Ingredient("tool", "bwa")])
    assert result.unresolved == []
    assert len(result.clusters) == 2
    all_names = {m.name for c in result.clusters for m in c.modules}
    assert {"SAMtools", "BWA"} <= all_names


def test_minimization_is_per_cluster(sample_graph):
    # samtools (GCC-12.3.0, dep zlib) and bwa (GCC-13.2.0, no deps) land in
    # two separate clusters; minimization runs independently in each, so the
    # SAMtools cluster drops zlib and the BWA cluster is just BWA.
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools"),
                          solver.Ingredient("tool", "bwa")])
    assert len(result.clusters) == 2
    by_root = {frozenset(m.name for m in c.modules) for c in result.clusters}
    assert by_root == {frozenset({"SAMtools"}), frozenset({"BWA"})}


def test_pin_exact_toolchain_resolves(sample_graph):
    # Pin GCC-12.3.0; samtools (GCC-12.3.0) is usable in that environment.
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools")],
                         pinned_toolchain="GCC-12.3.0")
    assert result.unresolved == []
    chosen = {m.full_name for c in result.clusters for m in c.modules}
    assert "SAMtools/1.18-GCC-12.3.0" in chosen


def test_pin_toolchain_excludes_super_toolchain(sample_graph):
    # Pin GCC-12.3.0; pandas comes from SciPy-bundle (gfbf-2023a), which is a
    # super-toolchain, not a sub-toolchain -> not usable -> unresolved.
    pandas = solver.Ingredient("python", "pandas")
    result = solver.cook(sample_graph, [pandas],
                         pinned_toolchain="GCC-12.3.0")
    assert pandas in result.unresolved


def test_pin_super_toolchain_includes_sub_toolchain(sample_graph):
    # Pin gfbf-2023a; GCC-12.3.0 is a sub-toolchain, so samtools resolves.
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools")],
                         pinned_toolchain="gfbf-2023a")
    assert result.unresolved == []


def test_full_emits_transitive_chain(sample_graph):
    result = solver.cook(sample_graph,
                         [solver.Ingredient("tool", "samtools")], full=True)
    names = [m.name for c in result.clusters for m in c.modules]
    assert names == ["zlib", "SAMtools"]  # deps-first, full transitive list
