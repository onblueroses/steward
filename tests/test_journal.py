"""Lesson 5: every write is journaled before it is sent, and can be undone."""

import json

import pytest

from conftest import labels, only_issues
from steward import Steward
from steward.models import ScriptedModel
from steward.tools import Toolbox, ToolFailure, TurnState, undo_write


def tools(root, dry_run=False):
    s = Steward(root, ScriptedModel([]), dry_run=dry_run)
    return s, Toolbox(s, TurnState(turn=1, offered=set(), now=1.0))


def comments(root, issue_id):
    return json.loads((root / "tracker" / "issues" / f"{issue_id}.json").read_text())["comments"]


def test_a_label_write_records_before_and_after(root):
    s, tb = tools(root)
    assert tb.call("set_labels", {"issue_id": "2", "labels": ["enhancement"]})["ok"]
    entry = s.journal.entry("j-1")
    assert entry["before"] == [] and entry["intended"] == ["enhancement"]
    assert entry["end"]["ok"] is True and entry["end"]["after"] == ["enhancement"]


def test_undo_restores_the_labels(root):
    s, tb = tools(root)
    tb.call("set_labels", {"issue_id": "2", "labels": ["enhancement"]})
    assert tb.call("undo", {"journal_id": "j-1"})["ok"]
    assert labels(root, "2") == []
    assert s.journal.entry("j-2")["undo_of"] == "j-1"


def test_undo_refuses_when_the_state_changed_since(root):
    s, tb = tools(root)
    tb.call("set_labels", {"issue_id": "2", "labels": ["enhancement"]})
    path = root / "tracker" / "issues" / "2.json"
    issue = json.loads(path.read_text())
    issue["labels"] = ["question"]
    path.write_text(json.dumps(issue))
    with pytest.raises(ToolFailure, match="changed since"):
        undo_write(s, "j-1", 2.0)
    assert labels(root, "2") == ["question"]


def test_a_comment_is_corrected_by_a_further_comment(root):
    s, tb = tools(root)
    tb.call("comment", {"issue_id": "2", "text": "Please add a mock-up."})
    tb.call("undo", {"journal_id": "j-1"})
    texts = [c["text"] for c in comments(root, "2")]
    assert texts == ["Please add a mock-up.", "Correction: please ignore my earlier comment (j-1)."]


def test_a_dry_run_journals_the_intent_and_sends_nothing(root):
    s, tb = tools(root, dry_run=True)
    assert tb.call("set_labels", {"issue_id": "2", "labels": ["enhancement"]})["ok"]
    assert labels(root, "2") == []
    assert s.journal.entry("j-1")["dry_run"] is True
    with pytest.raises(ToolFailure, match="dry run"):
        undo_write(s, "j-1", 2.0)


def test_a_failed_read_writes_nothing(root):
    s, tb = tools(root)
    result = tb.call("set_labels", {"issue_id": "99", "labels": ["bug"]})
    assert result["ok"] is False
    assert s.journal.writes() == []


def test_an_open_entry_becomes_an_event_at_start(root):
    only_issues(root, "2")
    s, _ = tools(root)
    s.journal.begin("labels", "2", [], ["bug"], False, 1.0)
    s.collect(2.0)
    (event,) = [e for e in s.inbox.pending() if e.kind == "open_write"]
    assert event.data["journal_id"] == "j-1"
    assert "outcome unknown" in event.data["note"]


def test_there_is_no_delete_send_or_close_tool(root):
    _, tb = tools(root)
    names = {spec["function"]["name"] for spec in tb.specs()}
    assert names == set(Toolbox.NAMES)
    assert not any(word in name for name in names for word in ("delete", "remove", "send", "close"))
