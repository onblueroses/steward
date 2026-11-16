"""Lesson 7: ask the operator a ready decision, store it, accept one answer, wake on it."""

import pytest

from conftest import call, only_issues, reply
from steward import Steward
from steward.models import ScriptedModel
from steward.questions import QuestionError
from steward.tools import Toolbox, TurnState
from steward.triage import DemoModel


@pytest.fixture
def asked(root):
    only_issues(root, "4")
    s = Steward(root, DemoModel())
    s.run(now=1.0)
    (q,) = s.questions.all()
    return s, q


def test_the_demo_asks_with_context_options_and_a_recommendation(asked):
    _, q = asked
    assert q["status"] == "open" and q["context"]
    assert q["recommended"] in q["options"] and len(q["options"]) >= 2


def test_a_question_without_a_valid_recommendation_is_refused(root):
    s = Steward(root, DemoModel())
    s.collect(1.0)
    event = s.inbox.pending()[0]
    state = TurnState(turn=1, offered={event.id}, now=1.0)
    bad = Toolbox(s, state).call("ask_operator", {"event_id": event.id, "question": "Which?", "context": "c",
                                                  "options": ["a", "b"], "recommended": "c"})
    assert bad["ok"] is False
    one = Toolbox(s, state).call("ask_operator", {"event_id": event.id, "question": "Which?", "context": "c",
                                                  "options": ["a"], "recommended": "a"})
    assert one["ok"] is False
    good = Toolbox(s, state).call("ask_operator", {"event_id": event.id, "question": "Which?", "context": "c",
                                                   "options": ["a", "b"], "recommended": "a"})
    assert good["ok"] is True


def test_only_one_answer_is_accepted(asked):
    s, q = asked
    uid = s.config.operator_uids[0]
    s.answer(q["id"], "question", uid=uid, now=2.0)
    with pytest.raises(QuestionError, match="already"):
        s.answer(q["id"], "bug", uid=uid, now=3.0)
    assert s.questions.get(q["id"])["answer"] == "question"


def test_an_answer_must_be_one_of_the_options(asked):
    s, q = asked
    with pytest.raises(QuestionError, match="not an option"):
        s.answer(q["id"], "wontfix", uid=s.config.operator_uids[0], now=2.0)


def test_the_answer_wakes_the_agent_and_repeats_until_done(asked):
    s, q = asked
    event = s.answer(q["id"], "question", uid=s.config.operator_uids[0], now=2.0)
    assert event.role == "operator" and s.gate.rule_for(event).index == 0

    ignoring = ScriptedModel([reply(content="I will look later.")])
    Steward(s.root, ignoring).run(now=3.0)
    assert ignoring.requests, "the answer started a turn"
    assert event.id in [e.id for e in Steward(s.root, DemoModel()).inbox.pending()]

    Steward(s.root, DemoModel()).run(now=4.0)
    assert Steward(s.root, DemoModel()).inbox.pending() == []
    assert (s.root / "tracker" / "issues" / "4.json").read_text().count('"question"') == 1


def test_an_answer_stored_before_a_kill_reaches_the_inbox_at_the_next_start(asked):
    s, q = asked
    s.questions.answer(q["id"], "bug", by="operator", now=2.0)  # the kill came before the inbox write
    assert [e for e in s.inbox.pending() if e.kind == "answer"] == []
    s.collect(3.0)
    (answer,) = [e for e in s.inbox.pending() if e.kind == "answer"]
    assert answer.data["answer"] == "bug"


def test_the_model_has_no_tool_to_answer_its_own_question(root):
    assert not any("answer" in name for name in Toolbox.NAMES)
