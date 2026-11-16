"""The demo model: a deterministic stand-in for a language model that triages issues.

It reads only what a real model reads: the messages and the tool results. It uses keyword
rules in place of judgement, so the demo and the tests run offline and give the same result
each time. Swap it for a real model with `--model openai`.
"""

from __future__ import annotations

import json
import re

from .models import Reply, ToolCall

# Words that put an issue outside the tracker's post, and the owner who gets the report.
# The owner names must match `[owners]` in steward.toml.
OUT_OF_POST = [
    (re.compile(r"\b(disk|server|outage|deploy)\b", re.I), "the infrastructure owner"),
    (re.compile(r"\b(ci|build pipeline|runner)\b", re.I), "the build owner"),
    (re.compile(r"\b(vulnerability|security|leak)\b", re.I), "the security owner"),
]
LABEL_RULES = [
    (re.compile(r"\b(crash\w*|error|exception|fails?|broken)\b", re.I), "bug"),
    (re.compile(r"\b(add|feature|support for|would be nice)\b", re.I), "enhancement"),
    (re.compile(r"\b(docs?|readme|typo)\b", re.I), "docs"),
]
REPRO = re.compile(r"steps to reproduce|to reproduce", re.I)


def _turn(messages: list[dict]) -> tuple[dict, list[tuple[ToolCall, dict]]]:
    """The turn message and the calls made so far in this turn, with their results."""
    turn = json.loads(messages[2]["content"].split("\n\n", 1)[1])
    results = {m["tool_call_id"]: json.loads(m["content"]) for m in messages if m.get("role") == "tool"}
    calls = []
    for m in messages:
        for c in m.get("tool_calls") or []:
            call = ToolCall(c["id"], c["function"]["name"], json.loads(c["function"]["arguments"]))
            calls.append((call, results.get(c["id"], {})))
    return turn, calls


class DemoModel:
    def __init__(self):
        self.counter = 0

    def _call(self, name: str, **arguments) -> ToolCall:
        self.counter += 1
        return ToolCall(f"demo-{self.counter}", name, arguments)

    def complete(self, messages: list[dict], tools: list[dict], purpose: str = "turn") -> Reply:
        if purpose == "fold":
            text = messages[-1]["content"]
            ids = sorted(set(re.findall(r'"issue_id":"(\w+)"', text)))
            kinds = sorted(set(re.findall(r"\] turn \d+ (\w+):", text)))
            return Reply(f"Earlier turns handled issues {', '.join(ids) or 'none'}. Entry kinds: {', '.join(kinds)}.")
        turn, calls = _turn(messages)
        read = {c.arguments.get("issue_id"): r for c, r in calls if c.name == "read_issue"}
        handled = {c.arguments.get("event_id") for c, _ in calls if c.name in ("done", "report_to_owner")}
        events = [e for e in turn["events"] if e["id"] not in handled]

        # Step 1: read every issue that an event is about, in one reply.
        reads = []
        for e in events:
            issue_id = e["data"].get("issue_id") or e["data"].get("about", {}).get("issue_id") \
                or (e["data"].get("target") if e["kind"] == "open_write" else None)
            if issue_id and issue_id not in read and issue_id not in [c.arguments["issue_id"] for c in reads]:
                reads.append(self._call("read_issue", issue_id=issue_id))
        if reads:
            return Reply("I read the issues first.", reads)

        # Step 2: decide each event. Reports end the turn, so they come last, one at a time.
        actions: list[ToolCall] = []
        report: ToolCall | None = None
        for e in events:
            got = self._decide(e, read)
            if got and got[0].name == "report_to_owner":
                report = report or got[0]
            elif got:
                actions += got
        if actions:
            return Reply("", actions)
        if report:
            return Reply("This is not in my post.", [report])
        return Reply("Turn complete.")

    def _decide(self, e: dict, read: dict) -> list[ToolCall]:
        eid, kind, data = e["id"], e["kind"], e["data"]
        if kind == "issue_opened":
            result = read.get(data["issue_id"], {})
            if not result.get("ok"):
                return [self._call("done", event_id=eid, outcome=f"Could not read issue {data['issue_id']}.")]
            issue = json.loads(result["result"])
            if issue["labels"]:
                return [self._call("done", event_id=eid, outcome=f"Issue {issue['id']} has labels already.")]
            text = f"{issue['title']}\n{issue.get('body', '')}"
            for pattern, owner in OUT_OF_POST:
                if pattern.search(text):
                    return [self._call("report_to_owner", event_id=eid, owner=owner,
                                       summary=f"Issue {issue['id']} ({issue['title']}) is about {owner.split()[1]}.")]
            for pattern, label in LABEL_RULES:
                if pattern.search(text):
                    if label == "bug" and not REPRO.search(text):
                        return [self._call("set_labels", issue_id=issue["id"], labels=["bug", "needs-info"]),
                                self._call("comment", issue_id=issue["id"],
                                           text="Thank you. Please add the steps to reproduce this bug."),
                                self._call("done", event_id=eid, outcome=f"Issue {issue['id']}: bug, asked for steps.")]
                    return [self._call("set_labels", issue_id=issue["id"], labels=[label]),
                            self._call("done", event_id=eid, outcome=f"Issue {issue['id']} labelled {label}.")]
            return [self._call("ask_operator", event_id=eid,
                               question=f"Which label fits issue {issue['id']}?",
                               context=f"Title: {issue['title']}. No keyword rule matched.",
                               options=["bug", "enhancement", "question"], recommended="question"),
                    self._call("done", event_id=eid, outcome=f"Issue {issue['id']}: asked the operator.")]
        if kind == "answer":
            issue_id = data["about"].get("issue_id")
            return [self._call("set_labels", issue_id=issue_id, labels=[data["answer"]]),
                    self._call("done", event_id=eid,
                               outcome=f"Issue {issue_id} labelled {data['answer']} on answer {data['question_id']}.")]
        if kind == "open_write":
            return [self._call("done", event_id=eid,
                               outcome=f"Checked {data['journal_id']} on issue {data['target']} after a restart.")]
        if kind == "issue_comment":
            return [self._call("done", event_id=eid, outcome=f"Read comment {data['index']} on issue {data['issue_id']}. No change.")]
        return [self._call("done", event_id=eid, outcome=f"Noted {kind}.")]
