"""One pass of the steward: collect events, ask the wake gate, run turns, write the stamp.

A pass runs when something starts it: a new event, a due time, the operator. It never polls a
model. The pass ends when the gate wakes nothing, or when a turn finished no event.
"""

from __future__ import annotations

import json
import os
import pwd
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import config as config_mod
from . import store
from .gate import Plan, WakeGate
from .inbox import Event, Inbox, Reminders, make_event
from .journal import Journal
from .memory import Record
from .models import Model, ModelError
from .questions import Questions
from .signing import Signer
from .tools import Toolbox, TurnState
from .tracker import LocalTracker, Tracker, TrackerError

FOLD_PROMPT = (
    "Summarize these entries of your own record for your later turns. Keep issue ids, "
    "question ids, journal ids, owners and decisions. Write full sentences."
)


class IdentityError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    state: Path

    @property
    def inbox(self) -> Path: return self.state / "inbox"
    @property
    def reminders(self) -> Path: return self.state / "reminders.jsonl"
    @property
    def journal(self) -> Path: return self.state / "journal.jsonl"
    @property
    def outcomes(self) -> Path: return self.state / "outcomes.jsonl"
    @property
    def reports(self) -> Path: return self.state / "reports.jsonl"
    @property
    def questions(self) -> Path: return self.state / "questions.sqlite"
    @property
    def key(self) -> Path: return self.state / "key"
    @property
    def stamp(self) -> Path: return self.state / "stamp.json"


@dataclass
class PassReport:
    turns: int = 0
    handled: list[str] = field(default_factory=list)
    waiting: list[tuple[str, float]] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def account(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid:{uid}"


class Steward:
    def __init__(self, root: Path, model: Model, *, dry_run: bool = False,
                 tracker: Tracker | None = None, clock=time.time):
        self.root = Path(root)
        self.config = config_mod.load(self.root / "steward.toml")
        self.paths = Paths(self.config.state_dir)
        self.model, self.dry_run, self.clock = model, dry_run, clock
        try:
            self.post = (self.root / "POST.md").read_text(encoding="utf-8")
            self.standing = (self.root / "STANDING.md").read_text(encoding="utf-8")
        except OSError as e:
            raise config_mod.ConfigError(f"a steward needs POST.md and STANDING.md: {e}") from e
        self.signer = Signer.load(self.paths.key, create=True)
        self.inbox = Inbox(self.paths.inbox)
        self.gate = WakeGate(self.config)
        self.reminders = Reminders(self.paths.reminders)
        self.record = Record(self.paths.state)
        self.journal = Journal(self.paths.journal)
        self.questions = Questions(self.paths.questions)
        self.tracker = tracker if tracker is not None else self._tracker()

    def _tracker(self) -> Tracker:
        gh = self.config.github
        if not gh.get("enabled", False):
            return LocalTracker(self.config.tracker_dir, self.signer)
        from .github import GitHubTracker
        token = os.environ.get(gh.get("token_env", "GITHUB_TOKEN"), "")
        if not token:
            raise TrackerError("the GitHub adapter is on, but its token variable is empty")
        return GitHubTracker(gh.get("repo", ""), token, list(gh.get("operator_logins", [])))

    # --- the operator's door: identity comes from the Unix account, never from text -----------

    def _operator(self, uid: int) -> str:
        if uid not in self.config.operator_uids:
            raise IdentityError(f"account {account(uid)} is not an operator of this steward")
        return account(uid)

    def tell(self, text: str, uid: int, now: float | None = None) -> Event:
        now = self.clock() if now is None else now
        sender = self._operator(uid)
        event = make_event("message", "operator", sender, "operator", now, {"text": text},
                           key=("message", sender, now, text))
        self.inbox.put(event)
        return event

    def answer(self, qid: str, option: str, uid: int, now: float | None = None) -> Event:
        now = self.clock() if now is None else now
        self.questions.answer(qid, option, self._operator(uid), now)
        event = self._answer_event(self.questions.get(qid) or {})
        self.inbox.put(event)
        return event

    def _answer_event(self, q: dict) -> Event:
        about = self.inbox.get(q["event_id"])
        data = {"question_id": q["id"], "question": q["question"], "answer": q["answer"],
                "about_event": q["event_id"], "about": about.data if about else {}}
        return make_event("answer", "operator", q["answered_by"], "operator", q["answered_at"], data,
                          key=("answer", q["id"]))

    # --- one pass --------------------------------------------------------------------------

    def collect(self, now: float) -> list[str]:
        """Put every new fact into the inbox. Returns failures; a failed read is never 'nothing new'."""
        events, failures = self.tracker.ingest(now)
        for event in events:
            self.inbox.put(event)
        for r in self.reminders.due(now):
            self.inbox.put(make_event("reminder", "steward", self.config.name, "self", r["due"],
                                      {"reminder_id": r["id"], "note": r["note"]}, key=("reminder", r["id"])))
        # An answer that reached the database but not the inbox (a kill in between) is sent again.
        for q in self.questions.all("answered"):
            self.inbox.put(self._answer_event(q))
        for j in self.journal.open_entries():
            self.inbox.put(make_event(
                "open_write", "journal", self.config.name, "self", now,
                {"journal_id": j["id"], "kind": j["kind"], "target": j["target"], "intended": j["intended"],
                 "note": "Write begun, outcome unknown. Read the target and check it."},
                key=("open_write", j["id"])))
        for name, lines in self.inbox.bad_lines().items():
            failures.append(f"{name}: lines {lines} do not parse")
        return failures

    def run(self, now: float | None = None) -> PassReport:
        now = self.clock() if now is None else now
        report = PassReport()
        report.failures += self.collect(now)
        for failure in report.failures:
            self.record.add(self.record.next_turn(), "failure", {"where": "collect", "error": failure}, now)
        while True:
            pending = self.inbox.pending()
            plan = self.gate.plan(pending, now)
            report.waiting = [(e.id, due) for e, due in plan.waiting]
            report.deferred = [e.id for e in plan.deferred]
            if not plan.wake:
                break
            # A waiting event is not offered yet: its rule asks for its due time first.
            offered = [e for e, _ in plan.wake] + plan.deferred
            state = self.turn(plan, offered, now)
            report.turns += 1
            report.handled += state.acked
            report.failures += state.failures
            if not state.acked:
                break
        self._stamp(report, now)
        return report

    def _stamp(self, report: PassReport, now: float) -> None:
        pending = self.inbox.pending()
        store.write_atomic(self.paths.stamp, json.dumps({
            "at": now, "ok": report.ok, "failures": report.failures, "turns": report.turns,
            "pending": len(pending), "oldest_pending_at": min((e.at for e in pending), default=None),
        }, indent=2) + "\n")

    def _summarize(self, text: str) -> str:
        try:
            reply = self.model.complete(
                [{"role": "system", "content": FOLD_PROMPT}, {"role": "user", "content": text}], [], purpose="fold")
        except Exception as e:
            raise ModelError(f"fold failed: {e!r}") from e
        if not reply.content.strip():
            raise ModelError("fold returned an empty summary")
        return reply.content.strip()

    def turn(self, plan: Plan, pending: list[Event], now: float) -> TurnState:
        number = self.record.next_turn()
        state = TurnState(turn=number, offered={e.id for e in pending}, now=now)
        tools = Toolbox(self, state)
        try:
            memory = self.record.compile(self.config.limit("context_budget_tokens"),
                                         self.config.limit("fold_chunk_tokens"), self._summarize)
        except ModelError as e:
            state.failures.append(str(e))
            self.record.add(number, "failure", {"where": "memory", "error": str(e)}, now)
            return state

        seen = {e["body"]["id"] for e in self.record.entries() if e["kind"] == "event"}
        for event in pending:
            if event.id not in seen:
                self.record.add(number, "event", event.to_row(), now)
        woken = [{"event": e.id, "rule": r.index} for e, r in plan.wake]
        self.record.add(number, "turn_start", {"woken": woken, "offered": sorted(state.offered)}, now)
        this_turn = {
            "now": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
            "woken": woken,
            "events": [e.to_row() for e in pending],
            "waiting": [{"event": e.id, "due": due} for e, due in plan.waiting],
            "open_questions": [{k: q[k] for k in ("id", "event_id", "question", "options")}
                               for q in self.questions.all("open")],
        }
        messages: list[dict] = [
            {"role": "system", "content": self.standing + "\n\n# Your post\n\n" + self.post},
            {"role": "user", "content": "# Your memory\n\n" + memory},
            {"role": "user", "content": "# This turn\n\n" + json.dumps(this_turn, indent=1, ensure_ascii=False)},
        ]
        specs = tools.specs()
        while not state.ended:
            try:
                reply = self.model.complete(messages, specs)
            except Exception as e:  # a failed model call is a failure, and no event is acked by it
                error = e if isinstance(e, ModelError) else ModelError(repr(e))
                state.failures.append(f"model: {error}")
                self.record.add(number, "failure", {"where": "model", "error": str(error)}, now)
                state.end_reason = "model failed"
                break
            calls = [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in reply.tool_calls]
            self.record.add(number, "assistant", {"content": reply.content, "tool_calls": calls}, now)
            if not reply.tool_calls:
                state.end_reason = "model ended the turn"
                break
            messages.append({"role": "assistant", "content": reply.content or None, "tool_calls": [
                {"id": c.id, "type": "function",
                 "function": {"name": c.name, "arguments": json.dumps(c.arguments)}} for c in reply.tool_calls]})
            for call in reply.tool_calls:
                if state.ended:
                    result = {"ok": False, "error": f"not run: the turn ended ({state.end_reason})"}
                else:
                    result = tools.call(call.name, call.arguments)
                self.record.add(number, "tool", {"id": call.id, "name": call.name,
                                                 "arguments": call.arguments, "result": result}, now)
                messages.append({"role": "tool", "tool_call_id": call.id,
                                 "content": json.dumps(result, ensure_ascii=False)})
                store.crashpoint("turn:after_tool")
        self.record.add(number, "turn_end", {"reason": state.end_reason, "acked": state.acked,
                                             "failures": state.failures}, now)
        return state
