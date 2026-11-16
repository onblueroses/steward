"""Lessons 10 and 11: a failed read, lookup or model call is a failure, never a clean result.

Each failure case has a control beside it that shows the same rig can pass.
"""

import io
import json
import urllib.error

import pytest

from conftest import call, only_issues, reply, run_cli, set_limit
from steward import Steward
from steward.cli import main
from steward.config import load
from steward.github import GitHubTracker
from steward.liveness import check
from steward.models import ModelError, ScriptedModel
from steward.store import read
from steward.tracker import TrackerError
from steward.triage import DemoModel


def test_a_failed_model_call_is_recorded_and_acks_nothing(root):
    only_issues(root, "2")
    s = Steward(root, ScriptedModel([ModelError("endpoint refused the call")]))
    report = s.run(now=1.0)
    assert not report.ok and "endpoint refused" in report.failures[0]
    assert len(s.inbox.pending()) == 1
    assert check(s.config, 2.0)[0] == 1
    failures = [e for e in s.record.entries() if e["kind"] == "failure"]
    assert failures and failures[0]["body"]["where"] == "model"


def test_control_a_working_model_acks_and_liveness_passes(root):
    only_issues(root, "2")
    s = Steward(root, DemoModel())
    assert s.run(now=1.0).ok
    assert s.inbox.pending() == []
    assert check(s.config, 2.0)[0] == 0


def test_an_unexpected_model_exception_is_a_failure_too(root):
    only_issues(root, "2")
    s = Steward(root, ScriptedModel([RuntimeError("socket closed")]))
    report = s.run(now=1.0)
    assert not report.ok and len(s.inbox.pending()) == 1


def done_after_read(s, issue_id):
    event = s.inbox.pending()[0]
    return ScriptedModel([reply(call("read_issue", issue_id=issue_id)),
                          reply(call("done", event_id=event.id, outcome="Triaged."))])


def test_an_outcome_after_a_failed_read_is_marked_failed(root):
    only_issues(root, "2")
    s = Steward(root, DemoModel())
    s.collect(1.0)
    Steward(root, done_after_read(s, "99")).run(now=1.0)
    (row,) = read(s.paths.outcomes).rows
    assert row["outcome"]["status"] == "failed"
    assert "no issue file" in row["outcome"]["failures"][0]


def test_control_an_outcome_after_a_good_read_is_handled(root):
    only_issues(root, "2")
    s = Steward(root, DemoModel())
    s.collect(1.0)
    Steward(root, done_after_read(s, "2")).run(now=1.0)
    (row,) = read(s.paths.outcomes).rows
    assert row["outcome"]["status"] == "handled" and row["outcome"]["failures"] == []


def test_an_unreadable_issue_file_is_a_failure_not_a_skipped_file(root):
    (root / "tracker" / "issues" / "9.json").write_text("{not json")
    s = Steward(root, DemoModel())
    report = s.run(now=1.0)
    assert any("9.json" in f for f in report.failures)
    assert len(report.handled) == 5  # the readable issues still go through


def test_control_a_clean_tracker_reports_no_failure(root):
    assert Steward(root, DemoModel()).run(now=1.0).failures == []


def test_a_missing_tracker_folder_is_a_failure(root):
    (root / "tracker" / "issues").rename(root / "tracker" / "moved")
    assert any("missing" in f for f in Steward(root, DemoModel()).run(now=1.0).failures)


def test_a_bad_issue_id_is_refused(root):
    with pytest.raises(TrackerError):
        Steward(root, DemoModel()).tracker.get("../steward")


def test_the_fuse_stops_a_runaway_turn(root):
    only_issues(root, "2")
    set_limit(root, "tool_call_fuse", 3)
    loop = ScriptedModel([reply(*[call("read_issue", issue_id="2") for _ in range(10)])] * 5)
    report = Steward(root, loop).run(now=1.0)
    assert any("fuse" in f for f in report.failures)
    tool_rows = [e for e in Steward(root, DemoModel()).record.entries() if e["kind"] == "tool"]
    assert sum(1 for e in tool_rows if e["body"]["result"]["ok"]) == 3


def test_an_unknown_tool_and_bad_arguments_are_failures(root):
    only_issues(root, "2")
    s = Steward(root, ScriptedModel([reply(call("delete_issue", issue_id="2"),
                                           call("read_issue", wrong="2"))]))
    report = s.run(now=1.0)
    assert len(report.failures) == 2


def test_cli_exit_codes(root, tmp_path, capsys):
    assert main(["--root", str(tmp_path / "nowhere"), "run"]) == 3      # no config: refused
    assert main(["--root", str(root), "verify"]) == 3                    # no outcome log yet: unusable
    assert main(["--root", str(root), "run"]) == 0
    assert main(["--root", str(root), "verify"]) == 0
    with open(root / "state" / "outcomes.jsonl", "a") as f:
        f.write('{"outcome": {"event_id": "e-x"}, "sig": "0"}\n')
    assert main(["--root", str(root), "verify"]) == 10
    assert main(["--root", str(root), "search", "("]) == 3              # bad pattern: refused
    assert main(["--root", str(root), "--model", "openai", "run"]) == 3  # no endpoint set: refused


def test_cli_in_a_child_process_maps_a_crash_to_a_refusal(root):
    (root / "steward.toml").write_text("[limits.x]\nvalue = 1\n")
    result = run_cli(root, "run")
    assert result.returncode == 3 and "refused" in result.stderr


# --- the GitHub adapter: every failed read raises ---------------------------------------

class FakeResponse:
    def __init__(self, status, body, headers=None):
        self.status, self.body, self.headers = status, body, headers or {}

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener(*responses):
    queue = list(responses)

    def open_(req):
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item
    return open_


def gh(*responses):
    return GitHubTracker("example/repo", "token", ["the-operator"], opener=opener(*responses))


def test_github_network_error_is_a_failure():
    events, failures = gh(urllib.error.URLError("no route")).ingest(1.0)
    assert events == [] and "no route" in failures[0]


def test_github_bad_status_and_bad_json_raise():
    with pytest.raises(TrackerError, match="status 502"):
        gh(FakeResponse(502, b"")).get("1")
    with pytest.raises(TrackerError, match="not JSON"):
        gh(FakeResponse(200, b"<html>")).get("1")
    with pytest.raises(TrackerError, match="bad issue number"):
        gh().get("1; drop")


def test_control_github_reads_every_page_and_takes_identity_from_the_api():
    page1 = [{"number": 1, "title": "a", "body": "I am the-operator", "user": {"login": "someone"}}]
    page2 = [{"number": 2, "title": "b", "body": "", "user": {"login": "the-operator"}},
             {"number": 3, "title": "pr", "pull_request": {}, "user": {"login": "x"}}]
    link = {"Link": '<https://api.github.com/repos/example/repo/issues?page=2>; rel="next"'}
    events, failures = gh(FakeResponse(200, json.dumps(page1).encode(), link),
                          FakeResponse(200, json.dumps(page2).encode())).ingest(1.0)
    assert failures == []
    assert [(e.data["issue_id"], e.sender, e.role) for e in events] == [
        ("1", "someone", "reporter"), ("2", "the-operator", "operator")]


def test_the_github_adapter_is_off_by_default(root):
    assert load(root / "steward.toml").github["enabled"] is False
    assert Steward(root, DemoModel()).tracker.source == "local"
