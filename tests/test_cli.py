import os
from modchef import cli

FIX_TTL = os.path.join(os.path.dirname(__file__), "fixtures", "sample.ttl")

def test_cook_prints_module_loads(capsys):
    rc = cli.main(["cook", "--tools", "samtools", "bcftools",
                   "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert rc == 0
    assert "module load SAMtools/1.18-GCC-12.3.0" in out

def test_cook_explain(capsys):
    cli.main(["cook", "--tools", "samtools", "--explain", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "requested tool: samtools" in out

def test_cook_output_file(tmp_path):
    out_file = tmp_path / "env.sh"
    rc = cli.main(["cook", "--tools", "samtools", "--name", "qc",
                   "--output", str(out_file), "--graph", FIX_TTL])
    assert rc == 0
    content = out_file.read_text()
    assert content.startswith("#!/bin/bash")
    assert "module load SAMtools/1.18-GCC-12.3.0" in content

def test_cook_from_recipe_file(tmp_path):
    rec = tmp_path / "r.yaml"
    rec.write_text("name: qc\ntools:\n  - samtools\n  - bcftools\n")
    rc = cli.main(["cook", str(rec), "--graph", FIX_TTL])
    assert rc == 0

def test_search_tool(capsys):
    cli.main(["search", "samtools", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "SAMtools/1.18-GCC-12.3.0" in out

def test_search_python_package(capsys):
    cli.main(["search", "--python", "pandas", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "SciPy-bundle" in out

def test_menu_lists_known_software(capsys):
    cli.main(["menu", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "samtools" in out.lower()

def test_inspect_shows_toolchain_and_deps(capsys):
    cli.main(["inspect", "SAMtools/1.18-GCC-12.3.0", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "GCC-12.3.0" in out
    assert "zlib/1.2.13" in out

def test_ingredients_lists_bundle_contents(capsys):
    cli.main(["ingredients", "SciPy-bundle/2023.07-gfbf-2023a", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "pandas" in out
    assert "numpy" in out

def test_cook_unresolved_exits_nonzero():
    rc = cli.main(["cook", "--tools", "doesnotexist123", "--graph", FIX_TTL])
    assert rc == 1


def test_cook_resolvable_exits_zero():
    rc = cli.main(["cook", "--tools", "samtools", "--graph", FIX_TTL])
    assert rc == 0


def test_explain_module_describes_provision(capsys):
    cli.main(["explain", "SciPy-bundle/2023.07-gfbf-2023a", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "pandas" in out
    assert "gfbf-2023a" in out


def test_cook_suggests_for_unresolved_tool(capsys):
    rc = cli.main(["cook", "--tools", "samtool", "--graph", FIX_TTL])
    captured = capsys.readouterr()
    assert rc == 1
    # the "did you mean" hint goes to stderr so stdout stays a clean recipe
    assert "samtools" in captured.err
    assert "did you mean" in captured.err.lower()


def test_cook_suggests_for_unresolved_python_package(capsys):
    cli.main(["cook", "--python", "pandus", "--graph", FIX_TTL])
    assert "pandas" in capsys.readouterr().err


def test_search_suggests_when_no_match(capsys):
    # 'samtoolz' is a near-miss that is NOT a substring of any name,
    # so substring search misses and the fuzzy hint kicks in
    rc = cli.main(["search", "samtoolz", "--graph", FIX_TTL])
    captured = capsys.readouterr()
    assert rc == 1
    assert "samtools" in (captured.out + captured.err)


def test_inspect_suggests_close_module(capsys):
    rc = cli.main(["inspect", "SAMtools/1.18-GCC-12.3.1", "--graph", FIX_TTL])
    captured = capsys.readouterr()
    assert rc == 1
    assert "SAMtools/1.18-GCC-12.3.0" in captured.err


def test_cook_needs_install_warns_and_exits_nonzero(capsys):
    rc = cli.main(["cook", "--tools", "star", "--graph", FIX_TTL])
    captured = capsys.readouterr()
    assert rc == 1
    assert "ask support to install" in captured.err
    assert "STAR/2.7.11a-GCC-12.3.0" in captured.err

def test_search_annotates_installed(capsys):
    cli.main(["search", "samtools", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "SAMtools/1.18-GCC-12.3.0" in out
    assert "[installed]" in out

def test_search_annotates_available(capsys):
    cli.main(["search", "star", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "available — not installed" in out

def test_cook_prepends_module_purge(capsys):
    cli.main(["cook", "--tools", "samtools", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "module purge"

def test_cook_minimal_by_default(capsys):
    cli.main(["cook", "--tools", "samtools", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "module load SAMtools/1.18-GCC-12.3.0" in out
    assert "zlib" not in out  # dep auto-loaded by Lmod, not emitted

def test_cook_full_emits_deps(capsys):
    cli.main(["cook", "--tools", "samtools", "--full", "--graph", FIX_TTL])
    out = capsys.readouterr().out
    assert "module load zlib/1.2.13-GCCcore-12.3.0" in out
    assert "module load SAMtools/1.18-GCC-12.3.0" in out
