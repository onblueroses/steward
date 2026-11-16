"""Lesson 4: fold memory over a full permanent record, and search the raw record."""

import pytest

from conftest import call, only_issues, reply, set_limit
from steward import Steward
from steward.memory import SearchError
from steward.models import ModelError, ScriptedModel
from steward.tools import Toolbox, TurnState


def chat(root, texts, model_steps=None):
    """One operator message and one turn for each text."""
    s = Steward(root, ScriptedModel([]))
    requests = []
    for i, text in enumerate(texts):
        msg = s.tell(text, uid=s.config.operator_uids[0], now=100.0 + i)
        model = ScriptedModel([reply(call("done", event_id=msg.id, outcome=f"Noted message {i}."))])
        Steward(root, model).run(now=100.0 + i)
        requests += model.requests
    return requests


@pytest.fixture
def small(root):
    only_issues(root)
    set_limit(root, "context_budget_tokens", 300)
    return root


def test_a_folded_fact_leaves_the_context_and_stays_in_the_search(small):
    texts = ["Hello, steward.", "The spare key code is 4711."] + [f"Routine note number {i}, nothing new." for i in range(8)]
    requests = chat(small, texts)
    s = Steward(small, ScriptedModel([]))
    assert s.record.folds(), "the budget forced at least one fold"
    last_context = requests[-1][1]["content"]
    assert "4711" not in last_context  # not in the head, not in the tail: folded away
    assert "Folded" in last_context
    assert "Hello, steward." in last_context  # the head stays verbatim
    assert any("4711" in line for line in s.record.search(r"\b4711\b"))


def test_folding_never_changes_the_record(small):
    chat(small, ["first message"] * 1)
    before = (small / "state" / "record.jsonl").read_text()
    chat(small, [f"later message {i}" for i in range(8)])
    after = (small / "state" / "record.jsonl").read_text()
    assert after.startswith(before)


def test_without_a_budget_nothing_folds(root):
    only_issues(root)
    text = (root / "steward.toml").read_text()
    start = text.index("[limits.context_budget_tokens]")
    end = text.index("[limits.fold_chunk_tokens]")
    (root / "steward.toml").write_text(text[:start] + text[end:])
    chat(root, [f"message {i}" for i in range(6)])
    assert Steward(root, ScriptedModel([])).record.folds() == []


def test_a_bad_pattern_is_a_failure_not_an_empty_result(small):
    chat(small, ["The spare key code is 4711."])
    s = Steward(small, ScriptedModel([]))
    with pytest.raises(SearchError):
        s.record.search("(unclosed")
    assert s.record.search("4711")  # positive control: a good pattern finds the fact


def test_the_search_tool_reports_a_bad_pattern_as_a_failure(small):
    s = Steward(small, ScriptedModel([]))
    state = TurnState(turn=1, offered=set(), now=1.0)
    result = Toolbox(s, state).call("history_search", {"pattern": "["})
    assert result["ok"] is False and state.failures
    good = Toolbox(s, TurnState(turn=1, offered=set(), now=1.0)).call("history_search", {"pattern": "x"})
    assert good["ok"] is True and "found no match" in good["result"]


class FoldFails(ScriptedModel):
    def complete(self, messages, tools, purpose="turn"):
        if purpose == "fold":
            raise ModelError("fold endpoint down")
        return super().complete(messages, tools, purpose)


def test_a_failed_fold_is_a_failure_and_the_events_stay_pending(small):
    chat(small, [f"message {i} with some padding text" for i in range(6)])
    s = Steward(small, ScriptedModel([]))
    msg = s.tell("one more", uid=s.config.operator_uids[0], now=500.0)
    folds_before = len(s.record.folds())
    report = Steward(small, FoldFails([reply(call("done", event_id=msg.id, outcome="x"))])).run(now=500.0)
    assert not report.ok and "fold endpoint down" in report.failures[0]
    assert msg.id in [e.id for e in s.inbox.pending()]
    assert len(s.record.folds()) == folds_before
