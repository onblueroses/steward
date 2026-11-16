"""Lesson 12: no unmeasured limits. Every number in the config is a limit with a source."""

import tomllib

import pytest

from conftest import EXAMPLE
from steward.config import LIMIT_KINDS, NOT_LIMITS, ConfigError, load


def numbers(node, path=()):
    if isinstance(node, bool):
        return
    if isinstance(node, (int, float)):
        yield path
    elif isinstance(node, dict):
        for k, v in node.items():
            yield from numbers(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from numbers(v, path + (str(i),))


def test_every_limit_in_the_example_config_has_a_source():
    raw = tomllib.loads((EXAMPLE / "steward.toml").read_text())
    assert raw["limits"], "the example has limits to check"
    for name, entry in raw["limits"].items():
        assert set(entry) >= {"value", "kind", "source"}, name
        assert entry["kind"] in LIMIT_KINDS, name
        assert entry["source"].strip(), name
    for path in numbers(raw):
        assert path[0] == "limits" or path[:2] in NOT_LIMITS, f"number outside [limits]: {path}"


def test_exactly_one_guard_and_it_is_named_as_a_guard():
    raw = tomllib.loads((EXAMPLE / "steward.toml").read_text())
    guards = [n for n, e in raw["limits"].items() if e["kind"] == "guard"]
    assert guards == ["tool_call_fuse"]
    assert "not a pace" in raw["limits"]["tool_call_fuse"]["source"]


def test_the_example_config_loads():
    config = load(EXAMPLE / "steward.toml")
    assert config.limit("tool_call_fuse") > 0
    assert config.limit("no_such_limit") is None


def write(tmp_path, text):
    path = tmp_path / "steward.toml"
    path.write_text(text)
    return path


def test_a_number_outside_limits_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="numbers outside"):
        load(write(tmp_path, "[model]\ntimeout = 30\n"))


def test_a_limit_without_a_source_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="no source"):
        load(write(tmp_path, '[limits.x]\nvalue = 3\nkind = "measured"\nsource = " "\n'))


def test_a_limit_with_an_unknown_kind_is_refused(tmp_path):
    with pytest.raises(ConfigError, match="kind"):
        load(write(tmp_path, '[limits.x]\nvalue = 3\nkind = "cautious default"\nsource = "felt right"\n'))


def test_a_wake_delay_must_name_a_limit(tmp_path):
    with pytest.raises(ConfigError, match="not in"):
        load(write(tmp_path, '[[wake]]\nmatch = {}\naction = "wake_after"\nafter_limit = "missing"\n'))


def test_a_config_with_a_sourced_limit_loads(tmp_path):
    config = load(write(tmp_path, '[limits.x]\nvalue = 3\nkind = "system"\nsource = "the API documents 3"\n'))
    assert config.limit("x") == 3
