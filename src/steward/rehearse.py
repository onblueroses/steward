"""Rehearsal: replay recorded events against the current post, standing file and model.

The rehearsal gets a fresh folder, a copy of the three steward files, and a tracker built from
the recording. Writes run in dry-run: they are journaled and never sent. Nothing in the
rehearsal can reach a live tracker or a live notification. The output is counts to read:
writes by kind and tool calls by name.
"""

from __future__ import annotations

import shutil
import time
from collections import Counter
from pathlib import Path

from . import store
from .inbox import Event
from .models import Model
from .steward import Steward
from .tracker import SnapshotTracker


def load_recording(path: Path) -> list[Event]:
    rows = store.read(path)
    if rows.bad_lines:
        raise ValueError(f"{path}: lines {rows.bad_lines} do not parse")
    return [Event(**{k: r[k] for k in Event.__dataclass_fields__ if k in r}) for r in rows.rows]


def rehearse(root: Path, recording: Path, model: Model, out: Path | None = None) -> dict:
    events = load_recording(recording)
    out = out or root / "rehearsals" / time.strftime("%Y%m%dT%H%M%S")
    out.mkdir(parents=True, exist_ok=False)
    for name in ("steward.toml", "POST.md", "STANDING.md"):
        shutil.copy(root / name, out / name)
    steward = Steward(out, model, dry_run=True, tracker=SnapshotTracker(events))
    for event in events:
        steward.inbox.put(event)
    passes = []
    while True:
        report = steward.run()
        passes.append(report)
        if not report.handled:
            break
    tools = Counter(e["body"]["name"] for e in steward.record.entries() if e["kind"] == "tool")
    writes = Counter(r["kind"] for r in steward.journal.writes())
    writes["report"] = len(store.read(steward.paths.reports).rows)
    writes["question"] = len(steward.questions.all())
    writes["reminder"] = len(store.read(steward.paths.reminders).rows)
    return {
        "folder": str(out),
        "events": len(events),
        "handled": sum(len(p.handled) for p in passes),
        "pending": len(steward.inbox.pending()),
        "turns": sum(p.turns for p in passes),
        "writes_by_kind": dict(sorted((k, v) for k, v in writes.items() if v)),
        "tool_calls_by_name": dict(sorted(tools.items())),
        "max_tool_calls_in_a_turn": max(_calls_per_turn(steward).values(), default=0),
        "failures": [f for p in passes for f in p.failures],
    }


def _calls_per_turn(steward: Steward) -> Counter:
    return Counter(e["turn"] for e in steward.record.entries() if e["kind"] == "tool")
