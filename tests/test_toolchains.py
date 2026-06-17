from modchef import toolchains

# A miniature tc_id-level hierarchy (edges: node -> its sub-toolchains):
# gfbf-2023a -> GCC-12.3.0 -> GCCcore-12.3.0
HIER = {
    "GCCcore-12.3.0": set(),
    "GCC-12.3.0": {"GCCcore-12.3.0"},
    "gfbf-2023a": {"GCC-12.3.0"},
    "gompi-2023a": {"GCC-12.3.0"},
    "GCC-13.2.0": {"GCCcore-13.2.0"},
    "GCCcore-13.2.0": set(),
}

def test_ancestors_includes_transitive_and_self():
    assert toolchains.ancestors("gfbf-2023a", HIER) == \
        {"gfbf-2023a", "GCC-12.3.0", "GCCcore-12.3.0"}

def test_ancestors_of_base():
    assert toolchains.ancestors("GCCcore-12.3.0", HIER) == {"GCCcore-12.3.0"}

def test_compatible_when_one_is_ancestor_of_other():
    # a GCC-12.3.0 module is loadable inside a gfbf-2023a environment
    assert toolchains.compatible("GCC-12.3.0", "gfbf-2023a", HIER) is True
    assert toolchains.compatible("gfbf-2023a", "GCC-12.3.0", HIER) is True

def test_incompatible_across_generations():
    # GCC-12.3.0 and GCC-13.2.0 are different generations -> not compatible
    assert toolchains.compatible("GCC-12.3.0", "GCC-13.2.0", HIER) is False

def test_resolve_chain_is_generation_aware():
    # Integration: real EasyBuild. gfbf-2023a resolves through GCC-12.3.0 only.
    chain = toolchains.resolve_chain("gfbf", "2023a")
    assert chain == ["GCCcore-12.3.0", "GCC-12.3.0", "gfbf-2023a"]
