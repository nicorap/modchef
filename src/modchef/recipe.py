"""Recipe model: ingredients from flags or a recipe.yaml file."""
from dataclasses import dataclass, field

import yaml

from modchef.solver import Ingredient


@dataclass
class Recipe:
    name: str = None
    ingredients: list = field(default_factory=list)


def from_flags(name=None, tools=None, python=None, r=None):
    ings = []
    for t in tools or []:
        ings.append(Ingredient("tool", t.lower()))
    for p in python or []:
        ings.append(Ingredient("python", p.lower()))
    for x in r or []:
        ings.append(Ingredient("r", x.lower()))
    return Recipe(name=name, ingredients=ings)


def load_recipe(path):
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    return from_flags(
        name=data.get("name"),
        tools=data.get("tools"),
        python=data.get("python"),
        r=data.get("r"),
    )
