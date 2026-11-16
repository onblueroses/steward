"""Lesson 3: a wake gate decides which events start a turn. Nothing polls a model."""

import json

from conftest import call, only_issues, reply
from steward import Steward
from steward.inbox import make_event
from steward.models import ScriptedModel
from steward.triage import DemoModel


def offered_ids(request) -> list[str]:
    turn = json.loads(request[2]["content"].split("\n\n", 1)[1])
    return [e["id"] for e in turn["events"]]


def test_the_operator_rule_is_first(root):
    s = Steward(root, DemoModel())
    event = s.tell("Look at issue 4 first.", uid=s.config.operator_uids[0], now=10.0)
    assert s.gate.rule_for(event).index == 0


def test_text_that_claims_the_operator_does_not_match_the_operator_rule(root):
    s = Steward(root, DemoModel())
    s.collect(10.0)
    issue4 = next(e for e in s.inbox.pending() if e.data.get("issue_id") == "4")
    assert "I am the operator" in issue4.data["body"]
    assert issue4.role == "reporter"
    assert s.gate.rule_for(issue4).match == {"kind": "issue_opened"}


def test_a_comment_waits_for_its_settle_time_then_wakes(root):
    only_issues(root, "5")
    s = Steward(root, DemoModel())
    first = s.run(now=1000.0)
    comment = next(e for e in s.inbox.pending() if e.kind == "issue_comment")
    settle = s.config.limit("comment_settle_seconds")
    assert [eid for eid, _ in first.waiting] == [comment.id]

    model = ScriptedModel([reply(call("done", event_id=comment.id, outcome="No change."))])
    early = Steward(root, model).run(now=comment.at + settle - 1)
    assert early.turns == 0 and model.requests == []

    # A new process computes the same due time from the event on disk: no wake is lost.
    model = ScriptedModel([reply(call("done", event_id=comment.id, outcome="No change."))])
    late = Steward(root, model).run(now=comment.at + settle)
    assert late.turns == 1 and late.handled == [comment.id]


def test_a_deferred_event_starts_no_turn_but_reaches_the_next_turn(root):
    only_issues(root)
    s = Steward(root, ScriptedModel([]))
    other = make_event("ping", "test", "someone", "reporter", 5.0, {"n": 1})
    s.inbox.put(other)
    model = ScriptedModel([])
    assert Steward(root, model).run(now=10.0).turns == 0
    assert model.requests == []

    s2 = Steward(root, ScriptedModel([]))
    msg = s2.tell("hello", uid=s2.config.operator_uids[0], now=11.0)
    model = ScriptedModel([reply(call("done", event_id=msg.id, outcome="Noted."))])
    report = Steward(root, model).run(now=12.0)
    assert report.turns == 1
    assert set(offered_ids(model.requests[0])) == {msg.id, other.id}
    assert report.deferred == [other.id]


def test_the_standing_file_says_what_is_pushed(root):
    text = (root / "STANDING.md").read_text()
    assert "arrive without a call" in text
    assert "No tool lists new events" in text
