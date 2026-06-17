from modchef import output
from modchef.graph import ModuleRef
from modchef.solver import Cluster, CookResult, Ingredient

def _result():
    mods = [ModuleRef("u1", "zlib/1.2.13-GCCcore-12.3.0", "zlib", "1.2.13",
                      "GCCcore-12.3.0"),
            ModuleRef("u2", "SAMtools/1.18-GCC-12.3.0", "SAMtools", "1.18",
                      "GCC-12.3.0")]
    c = Cluster(toolchain_id="GCC-12.3.0", modules=mods,
                reasons={"SAMtools/1.18-GCC-12.3.0": ["requested tool: samtools"]})
    return CookResult(clusters=[c], unresolved=[])

def test_module_load_lines():
    text = output.render(_result())
    assert "module load zlib/1.2.13-GCCcore-12.3.0" in text
    assert "module load SAMtools/1.18-GCC-12.3.0" in text
    # dependency loads before dependent
    assert text.index("zlib") < text.index("SAMtools")

def test_explain_annotates_reasons():
    text = output.render(_result(), explain=True)
    assert "requested tool: samtools" in text

def test_unresolved_warning():
    res = CookResult(clusters=[], unresolved=[Ingredient("tool", "nope")])
    text = output.render(res)
    assert "nope" in text
    assert "not found" in text.lower()

def test_needs_install_warning():
    avail = ModuleRef("u", "STAR/2.7.11a-GCC-12.3.0", "STAR", "2.7.11a",
                      "GCC-12.3.0", installed=False)
    res = CookResult(clusters=[], unresolved=[],
                     needs_install=[(Ingredient("tool", "star"), [avail])])
    text = output.render(res)
    assert "STAR/2.7.11a-GCC-12.3.0" in text
    assert "ask support" in text.lower()
    assert "not installed" in text.lower()

def test_script_wrapping_for_output_file():
    text = output.render(_result(), as_script=True, name="germline-qc")
    assert text.startswith("#!/bin/bash")
    assert "germline-qc" in text
