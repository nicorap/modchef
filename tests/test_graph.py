def test_load_and_count(sample_graph):
    assert len(sample_graph.all_software()) >= 3

def test_module_ref_carries_installed_flag(sample_graph):
    star = sample_graph.modules_providing("star", kind="tool")[0]
    assert star.installed is False
    sam = next(m for m in sample_graph.modules_providing("samtools", kind="tool"))
    assert sam.installed is True   # predicate absent -> default installed

def test_modules_providing_tool(sample_graph):
    mods = sample_graph.modules_providing("samtools", kind="tool")
    assert any(m.name == "SAMtools" for m in mods)
    sam = next(m for m in mods if m.name == "SAMtools")
    assert sam.toolchain_id == "GCC-12.3.0"

def test_modules_providing_python_package(sample_graph):
    mods = sample_graph.modules_providing("pandas", kind="python")
    assert any(m.name == "SciPy-bundle" for m in mods)

def test_dependencies_of(sample_graph):
    mods = sample_graph.modules_providing("samtools", kind="tool")
    sam = next(m for m in mods if m.name == "SAMtools")
    dep_names = [d.full_name for d in sample_graph.dependencies_of(sam.uri)]
    assert "zlib/1.2.13-GCCcore-12.3.0" in dep_names

def test_compatible_toolchains_closure(sample_graph):
    anc = sample_graph.compatible_toolchains("gfbf-2023a")
    assert "gfbf-2023a" in anc
    assert "GCC-12.3.0" in anc        # same 2023a generation compiler
    assert "GCCcore-12.3.0" in anc
