"""Lesson 2: events are on disk before anything acts, and stay pending until done."""

from steward.inbox import Inbox, make_event


def ev(n, kind="issue_opened"):
    return make_event(kind, "local", "someone", "reporter", 100.0 + n, {"issue_id": str(n)})


def test_an_event_is_pending_until_acked(tmp_path):
    inbox = Inbox(tmp_path)
    inbox.put(ev(1))
    inbox.put(ev(2))
    assert [e.data["issue_id"] for e in inbox.pending()] == ["1", "2"]
    inbox.ack(ev(1).id, "sig")
    assert [e.data["issue_id"] for e in inbox.pending()] == ["2"]


def test_the_same_event_is_stored_once(tmp_path):
    inbox = Inbox(tmp_path)
    assert inbox.put(ev(1)) is True
    assert inbox.put(ev(1)) is False
    assert len(inbox.pending()) == 1


def test_a_new_process_sees_the_same_pending_events(tmp_path):
    Inbox(tmp_path).put(ev(1))
    assert [e.id for e in Inbox(tmp_path).pending()] == [ev(1).id]


def test_a_torn_last_line_is_reported_and_does_not_swallow_the_next_event(tmp_path):
    inbox = Inbox(tmp_path)
    inbox.put(ev(1))
    with open(inbox.events_path, "a") as f:
        f.write('{"id": "e-torn", "kind": "iss')  # a kill in the middle of an append
    inbox.put(ev(2))
    assert [e.data["issue_id"] for e in inbox.pending()] == ["1", "2"]
    assert inbox.bad_lines() == {"events.jsonl": [2]}


def test_a_clean_inbox_reports_no_bad_lines(tmp_path):
    inbox = Inbox(tmp_path)
    inbox.put(ev(1))
    assert inbox.bad_lines() == {}
