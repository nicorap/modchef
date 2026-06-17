import os
from rdflib import Graph, RDF
from modchef import indexer, schema

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "eb")

def test_parse_bundle_extracts_facts():
    facts = indexer.parse_easyconfig(
        os.path.join(FIX, "SciPy-bundle-2023.07-gfbf-2023a.eb"))
    assert facts.name == "SciPy-bundle"
    assert facts.version == "2023.07"
    assert facts.toolchain_name == "gfbf"
    assert facts.toolchain_version == "2023a"
    assert facts.full_name == "SciPy-bundle/2023.07-gfbf-2023a"
    assert ("pandas", "python") in facts.packages
    assert ("numpy", "python") in facts.packages

def test_parse_records_module_import_name_as_package():
    # PyTorch's module is 'PyTorch' but it imports as 'torch' (options.modulename)
    facts = indexer.parse_easyconfig(
        os.path.join(FIX, "PyTorch-2.1.2-foss-2023a.eb"))
    assert ("torch", "python") in facts.packages

def test_import_name_indexed_as_package():
    files = [os.path.join(FIX, f) for f in os.listdir(FIX)]
    g = indexer.build_graph(files)
    q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "PyTorch" ; mc:providesPackage ?p .
          ?p mc:name "torch" ; mc:ecosystem "python" . }
    """
    assert bool(g.query(q))

def test_parse_tool_extracts_dependency():
    facts = indexer.parse_easyconfig(
        os.path.join(FIX, "SAMtools-1.18-GCC-12.3.0.eb"))
    assert facts.name == "SAMtools"
    assert facts.moduleclass == "bio"
    # the (zlib, 1.2.13) dep resolves to the GCCcore subtoolchain module
    assert "zlib/1.2.13-GCCcore-12.3.0" in facts.dependencies

def test_build_graph_emits_provides_and_toolchain():
    files = [os.path.join(FIX, f) for f in os.listdir(FIX)]
    g = indexer.build_graph(files)
    # SciPy-bundle provides pandas
    q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "SciPy-bundle" ; mc:providesPackage ?p .
          ?p mc:name "pandas" ; mc:ecosystem "python" . }
    """
    assert bool(g.query(q))
    # SAMtools is built with GCC-12.3.0
    q2 = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "SAMtools" ; mc:builtWith ?t .
          ?t mc:name "GCC" ; mc:version "12.3.0" . }
    """
    assert bool(g.query(q2))

def test_build_graph_adds_subtoolchain_edges():
    files = [os.path.join(FIX, f) for f in os.listdir(FIX)]
    g = indexer.build_graph(files, with_toolchain_hierarchy=True)
    q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?gfbf mc:name "gfbf" ; mc:subToolchainOf ?gcc . ?gcc mc:name "GCC" . }
    """
    assert bool(g.query(q))

OFFICIAL = os.path.join(os.path.dirname(__file__), "fixtures", "eb_official")


def _official_graph():
    """Installed = eb fixtures; available = eb_official fixtures (full parse)."""
    eb_files = [os.path.join(FIX, f) for f in os.listdir(FIX)]
    off_files = [os.path.join(OFFICIAL, f) for f in os.listdir(OFFICIAL)]
    try:
        return indexer.build_graph(
            eb_files, official_paths=off_files,
            official_robot_paths=OFFICIAL)
    finally:
        # The official pass reconfigures EB's robot path (global singleton);
        # reset it so it doesn't leak into other tests.
        indexer.configure_easybuild()
        indexer._CONFIGURED = False


def test_official_module_marked_not_installed():
    g = _official_graph()
    q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "SPAdes" ; mc:installed false . }
    """
    assert bool(g.query(q))

def test_official_bundle_exts_are_indexed():
    # full parse means a not-installed bundle still advertises its packages
    g = _official_graph()
    q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "Faker-bundle" ; mc:installed false ;
              mc:providesPackage ?p . ?p mc:name "faker" ; mc:ecosystem "python" . }
    """
    assert bool(g.query(q))

def test_official_module_deps_are_indexed():
    g = _official_graph()
    q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "SPAdes" ; mc:dependsOn ?d .
          ?d mc:fullName "zlib/1.2.13-GCCcore-12.3.0" . }
    """
    assert bool(g.query(q))

def test_official_pass_dedups_against_installed():
    # zlib is in both repos; it must stay installed=true (not downgraded).
    g = _official_graph()
    q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "zlib" ; mc:installed false . }
    """
    assert not bool(g.query(q))

def test_build_graph_marks_installed_true():
    files = [os.path.join(FIX, f) for f in os.listdir(FIX)]
    g = indexer.build_graph(files)
    q = """
    PREFIX mc: <https://modchef.dev/schema#>
    ASK { ?m mc:name "SAMtools" ; mc:installed true . }
    """
    assert bool(g.query(q))

def test_main_writes_ttl(tmp_path):
    out = tmp_path / "modchef.ttl"
    rc = indexer.main(["--repo", FIX, "--output", str(out)])
    assert rc == 0
    assert out.exists()
    g = Graph()
    g.parse(str(out), format="turtle")
    assert len(list(g.subjects(RDF.type, schema.MC.Module))) >= 3

def test_configure_easybuild_sets_robot_path(tmp_path):
    from easybuild.tools.config import build_option
    repo = tmp_path / "repo"
    repo.mkdir()
    try:
        indexer.configure_easybuild(str(repo))
        assert str(repo) in build_option("robot_path")
    finally:
        # Don't leak this (soon-deleted) tmp robot path into the EB singleton
        # that later tests share; reset to defaults and re-arm lazy config.
        indexer.configure_easybuild()
        indexer._CONFIGURED = False

def test_build_graph_configures_robot_path(monkeypatch):
    called = {}

    def fake_configure(robot_paths=None):
        called["robot_paths"] = robot_paths

    monkeypatch.setattr(indexer, "configure_easybuild", fake_configure)
    indexer.build_graph([], robot_paths="/some/repo")
    assert called["robot_paths"] == "/some/repo"
