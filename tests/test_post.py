"""Lesson 1: one owner, one domain. Out-of-post work gets one report, and the turn stops."""

import pytest

from conftest import labels, only_issues
from steward import Steward
from steward.store import read
from steward.tools import Toolbox, TurnState
from steward.triage import DemoModel


def test_the_post_has_three_sentences(root):
    lines = (root / "POST.md").read_text().splitlines()
    body = [line for line in lines if line and not line.startswith("#") and not line[0].isdigit()]
    steps = [line for line in lines if line[:1].isdigit()]
    assert len(body) == 3 and len(steps) == 3  # the third sentence lists its three steps


def test_an_out_of_post_event_gets_one_report_and_no_domain_write(root):
    only_issues(root, "3")
    s = Steward(root, DemoModel())
    report = s.run(now=1.0)
    reports = read(s.paths.reports).rows
    assert len(reports) == 1 and reports[0]["report"]["owner"] == "the infrastructure owner"
    assert s.journal.writes() == []
    assert labels(root, "3") == []
    assert len(report.handled) == 1
    ends = [e["body"]["reason"] for e in s.record.entries() if e["kind"] == "turn_end"]
    assert ends == ["reported to owner"]


def test_an_in_post_event_gets_a_domain_write_and_no_report(root):
    only_issues(root, "2")
    s = Steward(root, DemoModel())
    s.run(now=1.0)
    assert read(s.paths.reports).rows == []
    assert len(s.journal.writes()) == 1


def test_a_report_to_an_unknown_owner_is_refused(root):
    only_issues(root, "3")
    s = Steward(root, DemoModel())
    s.collect(1.0)
    event = s.inbox.pending()[0]
    state = TurnState(turn=1, offered={event.id}, now=1.0)
    result = Toolbox(s, state).call("report_to_owner", {"event_id": event.id, "owner": "whoever", "summary": "x"})
    assert result["ok"] is False and not state.ended
    assert read(s.paths.reports).rows == []


@pytest.mark.parametrize("kind", ["done", "report_to_owner"])
def test_an_event_that_was_not_offered_cannot_be_closed(root, kind):
    s = Steward(root, DemoModel())
    s.collect(1.0)
    event = s.inbox.pending()[0]
    args = {"event_id": event.id, "outcome": "x"} if kind == "done" else \
        {"event_id": event.id, "owner": "the build owner", "summary": "x"}
    result = Toolbox(s, TurnState(turn=1, offered=set(), now=1.0)).call(kind, args)
    assert result["ok"] is False
    assert event.id in [e.id for e in s.inbox.pending()]
