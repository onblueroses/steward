"""Lessons 2 and 5: a SIGKILL loses wakes, never events, and never a write's record.

Each test kills a real child process with SIGKILL, then starts a new process on the same
folder and checks what the inbox and the journal offer.
"""

import signal
import time

from conftest import labels, only_issues, run_cli, write_script
from steward import Steward
from steward.journal import Journal
from steward.store import read
from steward.triage import DemoModel


def pending_kinds(root):
    return sorted(e.kind for e in Steward(root, DemoModel()).inbox.pending())


def test_kill_mid_turn_keeps_the_event_pending_and_the_restart_finishes_it(root, tmp_path):
    only_issues(root, "2")
    marker = tmp_path / "blocked"
    script = write_script(tmp_path / "script.json", [
        {"tool_calls": [{"name": "set_labels", "arguments": {"issue_id": "2", "labels": ["enhancement"]}}]},
        "block",
    ])
    child = run_cli(root, "--model", f"script:{script}", "run", wait=False,
                    env={"STEWARD_BLOCK_MARKER": str(marker)})
    deadline = time.monotonic() + 30
    while not marker.exists():
        assert child.poll() is None, child.stderr.read()
        assert time.monotonic() < deadline, "the child never reached the blocking step"
        time.sleep(0.05)
    child.send_signal(signal.SIGKILL)
    assert child.wait() == -signal.SIGKILL

    # The write landed and its journal entry is closed. The event was never acked.
    assert labels(root, "2") == ["enhancement"]
    assert Journal(root / "state" / "journal.jsonl").open_entries() == []
    assert pending_kinds(root) == ["issue_opened"]

    restart = run_cli(root, "run")
    assert restart.returncode == 0, restart.stdout + restart.stderr
    assert pending_kinds(root) == []
    outcomes = read(root / "state" / "outcomes.jsonl").rows
    assert len(outcomes) == 1 and outcomes[0]["outcome"]["status"] == "handled"


def test_kill_between_journal_fsync_and_send_reports_an_open_write(root, tmp_path):
    only_issues(root, "2")
    script = write_script(tmp_path / "script.json", [
        {"tool_calls": [{"name": "set_labels", "arguments": {"issue_id": "2", "labels": ["enhancement"]}}]},
    ])
    child = run_cli(root, "--model", f"script:{script}", "run",
                    env={"STEWARD_CRASH_AT": "journal:after_fsync"})
    assert child.returncode == -signal.SIGKILL

    # The intent is on disk, the write was never sent.
    journal = Journal(root / "state" / "journal.jsonl")
    (entry,) = journal.open_entries()
    assert entry["kind"] == "labels" and entry["intended"] == ["enhancement"]
    assert labels(root, "2") == []

    restart = run_cli(root, "run")
    assert restart.returncode == 0, restart.stdout + restart.stderr
    assert "Checked j-1" in restart.stdout
    assert journal.open_entries() == []
    assert labels(root, "2") == ["enhancement"]
    assert pending_kinds(root) == []


def test_kill_between_outcome_and_ack_offers_the_event_again(root):
    only_issues(root, "2")
    child = run_cli(root, "run", env={"STEWARD_CRASH_AT": "done:after_outcome"})
    assert child.returncode == -signal.SIGKILL
    assert pending_kinds(root) == ["issue_opened"]

    restart = run_cli(root, "run")
    assert restart.returncode == 0
    assert pending_kinds(root) == []
    # At least once: the outcome is on disk twice, the event is acked once.
    assert len(read(root / "state" / "outcomes.jsonl").rows) == 2
    assert len(read(root / "state" / "inbox" / "acks.jsonl").rows) == 1


def test_kill_after_the_first_tool_call_loses_no_event(root):
    child = run_cli(root, "run", env={"STEWARD_CRASH_AT": "turn:after_tool"})
    assert child.returncode == -signal.SIGKILL
    assert pending_kinds(root).count("issue_opened") == 5

    restart = run_cli(root, "run")
    assert restart.returncode == 0
    assert pending_kinds(root) == ["issue_comment"]  # waits for its settle time, by rule
