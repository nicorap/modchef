from modchef import recipe
from modchef.solver import Ingredient

def test_parse_recipe_yaml(tmp_path):
    f = tmp_path / "r.yaml"
    f.write_text(
        "name: variant-qc\n"
        "tools:\n  - samtools\n  - bcftools\n"
        "python:\n  - pandas\n"
        "r:\n  - tidyverse\n")
    rec = recipe.load_recipe(str(f))
    assert rec.name == "variant-qc"
    assert Ingredient("tool", "samtools") in rec.ingredients
    assert Ingredient("python", "pandas") in rec.ingredients
    assert Ingredient("r", "tidyverse") in rec.ingredients

def test_from_flags():
    rec = recipe.from_flags(name="x", tools=["bwa"], python=["numpy"], r=[])
    assert rec.name == "x"
    assert Ingredient("tool", "bwa") in rec.ingredients
    assert Ingredient("python", "numpy") in rec.ingredients
