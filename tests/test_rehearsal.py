"""Lessons 8 and 9: rehearse on a recording with writes in dry-run, and read the counts."""

import re
import tomllib

import pytest

from conftest import EXAMPLE
from steward.cli import main
from steward.rehearse import rehearse
from steward.store import read
from steward.triage import DemoModel


@pytest.fixture
def result(root, tmp_path):
    return rehearse(root, EXAMPLE / "recording.jsonl", DemoModel(), out=tmp_path / "rehearsal")


def test_the_rehearsal_handles_the_recording_without_failure(result):
    assert result["failures"] == []
    assert result["handled"] == result["events"] and result["pending"] == 0


def test_the_rehearsal_prints_writes_by_kind_and_calls_by_name(result):
    assert result["writes_by_kind"]["labels"] > 0 and result["writes_by_kind"]["report"] == 3
    assert result["tool_calls_by_name"]["done"] > 0


def test_rehearsal_writes_are_journaled_and_never_sent(result, root, tmp_path):
    journal = read(tmp_path / "rehearsal" / "state" / "journal.jsonl").rows
    begins = [r for r in journal if r["phase"] == "begin"]
    assert begins and all(r["dry_run"] for r in begins)
    reports = read(tmp_path / "rehearsal" / "state" / "reports.jsonl").rows
    assert all(r["report"]["dry_run"] for r in reports)
    # The live steward folder is untouched.
    assert not (root / "state").exists()


def test_the_rehearsal_folds_memory(result, tmp_path):
    assert read(tmp_path / "rehearsal" / "state" / "folds.jsonl").rows


def test_the_fuse_source_matches_the_measured_busiest_turn(result):
    raw = tomllib.loads((EXAMPLE / "steward.toml").read_text())
    source = raw["limits"]["tool_call_fuse"]["source"]
    measured = int(re.search(r"makes (\d+) tool calls", source).group(1))
    assert measured == result["max_tool_calls_in_a_turn"]
    assert raw["limits"]["tool_call_fuse"]["value"] > measured


def test_the_demo_runs_end_to_end(tmp_path, capsys):
    assert main(["demo", str(tmp_path / "demo")]) == 0
    out = capsys.readouterr().out
    assert "reported to the infrastructure owner" in out
    assert "all valid" in out
