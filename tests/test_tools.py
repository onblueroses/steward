"""Tool specs come from type hints and docstrings. The docstrings are what the model reads."""

import pytest

from steward.models import tool_spec
from steward.tools import Toolbox


def test_tool_specs_have_types_and_required_parameters():
    spec = tool_spec(Toolbox.__new__(Toolbox).set_labels)
    params = spec["function"]["parameters"]
    assert spec["function"]["name"] == "set_labels"
    assert params["properties"]["labels"] == {"type": "array", "items": {"type": "string"}}
    assert set(params["required"]) == {"issue_id", "labels"}


def test_an_unsupported_type_is_refused():
    def f(x: dict) -> str:
        return ""
    with pytest.raises(TypeError):
        tool_spec(f)


@pytest.mark.parametrize("name", Toolbox.NAMES)
def test_tool_descriptions_have_no_semicolons(name):
    doc = getattr(Toolbox, name).__doc__
    assert doc and ";" not in doc
