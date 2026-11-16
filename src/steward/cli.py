"""Command line. Exit codes: 0 ok, 1 a failure was recorded, 3 refused or unusable, 10 findings.

Any error that is not handled below ends with exit 3. A crash is a refusal, never a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from . import liveness, signing, store
from .config import ConfigError
from .journal import WriteFailed
from .memory import SearchError
from .models import ModelError, OpenAIModel, ScriptedModel
from .questions import QuestionError
from .steward import IdentityError, Steward
from .tools import ToolFailure, undo_write
from .tracker import TrackerError
from .triage import DemoModel

OK, FAILED, REFUSED = 0, 1, 3
EXAMPLE = Path(__file__).resolve().parents[2] / "example"


def make_model(spec: str):
    if spec == "demo":
        return DemoModel()
    if spec == "openai":
        return OpenAIModel.from_env()
    if spec.startswith("script:"):
        return ScriptedModel.from_file(Path(spec[len("script:"):]))
    raise ModelError(f"unknown model {spec!r}: use demo, openai or script:FILE")


def _steward(args) -> Steward:
    return Steward(Path(args.root), make_model(args.model), dry_run=getattr(args, "dry_run", False))


def _print_pass(steward, report) -> int:
    print(f"turns: {report.turns}, events done: {len(report.handled)}")
    done = set(report.handled)
    for row in store.read(steward.paths.outcomes).rows:
        o = row.get("outcome", {})
        if o.get("event_id") in done:
            print(f"  {o['event_id']} [{o['status']}] {o['outcome']}")
    for eid, due in report.waiting:
        print(f"waiting: {eid} until {time.strftime('%H:%M:%S', time.localtime(due))}")
    for eid in report.deferred:
        print(f"deferred: {eid} (offered in the next turn)")
    for f in report.failures:
        print(f"FAILURE: {f}")
    return OK if report.ok else FAILED


def cmd_init(args) -> int:
    target = Path(args.dir)
    if target.exists():
        print(f"{target} exists. Choose a new folder.")
        return REFUSED
    if not EXAMPLE.is_dir():
        print("init needs the example folder of a source checkout")
        return REFUSED
    shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns("recording.jsonl"))
    print(f"steward created in {target}. Next: steward --root {target} run")
    return OK


def cmd_run(args) -> int:
    if args.replay:
        from .rehearse import rehearse
        result = rehearse(Path(args.root), Path(args.replay), make_model(args.model))
        print(json.dumps(result, indent=2))
        return OK if not result["failures"] else FAILED
    steward = _steward(args)
    return _print_pass(steward, steward.run())


def cmd_questions(args) -> int:
    s = _steward(args)
    rows = s.questions.all()
    for q in rows:
        mark = f"answered {q['answer']}" if q["status"] == "answered" else f"recommended {q['recommended']}"
        print(f"{q['id']} [{q['status']}] {q['question']} options {q['options']} ({mark})\n    {q['context']}")
    if not rows:
        print("no questions")
    return OK


def cmd_answer(args) -> int:
    event = _steward(args).answer(args.qid, args.option, os.getuid())
    print(f"answer stored. Event {event.id} wakes the steward at its next run.")
    return OK


def cmd_tell(args) -> int:
    event = _steward(args).tell(args.text, os.getuid())
    print(f"message stored as event {event.id}")
    return OK


def cmd_verify(args) -> int:
    s = _steward(args)
    code, lines = signing.verify_log(s.paths.outcomes, s.paths.key)
    rcode, rlines = signing.verify_log(s.paths.reports, s.paths.key, "report") if s.paths.reports.exists() else (0, [])
    for line in lines + rlines:
        print(line)
    return max(code, rcode, key=lambda c: {0: 0, 10: 1, 3: 2}[c])


def cmd_search(args) -> int:
    for line in _steward(args).record.search(args.pattern):
        print(line)
    return OK


def cmd_undo(args) -> int:
    s = _steward(args)
    s._operator(os.getuid())
    print(undo_write(s, args.journal_id, time.time()))
    return OK


def cmd_status(args) -> int:
    s = _steward(args)
    plan = s.gate.plan(s.inbox.pending(), time.time())
    print(f"pending events: {len(plan.wake) + len(plan.waiting) + len(plan.deferred)}"
          f" (wake now {len(plan.wake)}, waiting {len(plan.waiting)}, deferred {len(plan.deferred)})")
    for j in s.journal.open_entries():
        print(f"open write {j['id']}: {j['kind']} on {j['target']}, outcome unknown")
    print(f"open questions: {len(s.questions.all('open'))}")
    print(f"journaled writes: {len(s.journal.writes())}, outcomes: {len(store.read(s.paths.outcomes).rows)}")
    return OK


def cmd_liveness(args) -> int:
    from .config import load
    code, lines = liveness.check(load(Path(args.root) / "steward.toml"), time.time())
    print("\n".join(lines))
    return code


def cmd_demo(args) -> int:
    """The five-minute demo in one command: init, run, answer, run, verify."""
    folder = Path(args.dir)
    steps = [
        ("init", ["init", str(folder)]),
        ("first pass", ["--root", str(folder), "run"]),
        ("questions", ["--root", str(folder), "questions"]),
        ("answer q-1", ["--root", str(folder), "answer", "q-1", "question"]),
        ("second pass", ["--root", str(folder), "run"]),
        ("verify outcomes", ["--root", str(folder), "verify"]),
        ("status", ["--root", str(folder), "status"]),
    ]
    for title, argv in steps:
        print(f"\n$ steward {' '.join(argv)}    # {title}")
        code = main(argv)
        if code != OK:
            return code
    return OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="steward", description="A persistent owner agent for one domain.")
    p.add_argument("--root", default=".", help="folder with steward.toml, POST.md and STANDING.md")
    p.add_argument("--model", default="demo", help="demo (default), openai, or script:FILE")
    sub = p.add_subparsers(dest="command", required=True)
    sp = sub.add_parser("init", help="copy the example steward into a new folder")
    sp.add_argument("dir")
    sp.set_defaults(fn=cmd_init)
    sp = sub.add_parser("run", help="one pass: collect events, wake, run turns")
    sp.add_argument("--dry-run", action="store_true", help="journal writes but do not send them")
    sp.add_argument("--replay", metavar="FILE", help="rehearse on recorded events, writes in dry-run")
    sp.set_defaults(fn=cmd_run)
    sub.add_parser("questions", help="list questions to the operator").set_defaults(fn=cmd_questions)
    sp = sub.add_parser("answer", help="answer a question as the operator")
    sp.add_argument("qid")
    sp.add_argument("option")
    sp.set_defaults(fn=cmd_answer)
    sp = sub.add_parser("tell", help="send the steward a message as the operator")
    sp.add_argument("text")
    sp.set_defaults(fn=cmd_tell)
    sub.add_parser("verify", help="check the signatures of outcomes and reports").set_defaults(fn=cmd_verify)
    sp = sub.add_parser("search", help="search the raw record with a regular expression")
    sp.add_argument("pattern")
    sp.set_defaults(fn=cmd_search)
    sp = sub.add_parser("undo", help="undo one journaled write as the operator")
    sp.add_argument("journal_id")
    sp.set_defaults(fn=cmd_undo)
    sub.add_parser("status", help="pending events, open writes, open questions").set_defaults(fn=cmd_status)
    sub.add_parser("liveness", help="outside check of the last pass").set_defaults(fn=cmd_liveness)
    sp = sub.add_parser("demo", help="run the five-minute demo in a new folder")
    sp.add_argument("dir")
    sp.set_defaults(fn=cmd_demo)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (ConfigError, ModelError, QuestionError, IdentityError, ToolFailure, TrackerError,
            signing.SigningKeyError, SearchError, WriteFailed) as e:
        print(f"refused: {e}", file=sys.stderr)
        return REFUSED
    except Exception as e:  # anything unexpected is a refusal, never a pass
        print(f"refused: unexpected error {e!r}", file=sys.stderr)
        return REFUSED


if __name__ == "__main__":
    sys.exit(main())
