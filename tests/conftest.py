import os
import pytest
from modchef.graph import ModChefGraph

@pytest.fixture
def sample_graph():
    path = os.path.join(os.path.dirname(__file__), "fixtures", "sample.ttl")
    return ModChefGraph.load(path)
