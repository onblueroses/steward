"""Journaled writes: the state before and the intent are on disk before the write is sent.

Order of one write: read the state before, append a `begin` row and fsync it, send the write,
read the state back, append an `end` row. A kill between `begin` and `end` leaves an open entry.
At the next start an open entry becomes an `open_write` event: "write begun, outcome unknown".
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import store


class Journal:
    def __init__(self, path: Path):
        self.path = path

    def rows(self) -> list[dict]:
        return store.read(self.path).rows

    def begin(self, kind: str, target: str, before: Any, intended: Any, dry_run: bool,
              at: float, undo_of: str | None = None) -> str:
        number = sum(1 for r in self.rows() if r.get("phase") == "begin") + 1
        jid = f"j-{number}"
        store.append(self.path, {
            "id": jid, "phase": "begin", "kind": kind, "target": target, "before": before,
            "intended": intended, "dry_run": dry_run, "undo_of": undo_of, "at": at,
        })
        return jid

    def end(self, jid: str, ok: bool, after: Any = None, error: str | None = None) -> None:
        store.append(self.path, {"id": jid, "phase": "end", "ok": ok, "after": after, "error": error})

    def entry(self, jid: str) -> dict | None:
        begin = end = None
        for r in self.rows():
            if r.get("id") == jid:
                if r.get("phase") == "begin":
                    begin = r
                elif r.get("phase") == "end":
                    end = r
        if begin is None:
            return None
        return {**begin, "end": end}

    def open_entries(self) -> list[dict]:
        ended = {r["id"] for r in self.rows() if r.get("phase") == "end"}
        return [r for r in self.rows() if r.get("phase") == "begin" and r["id"] not in ended]

    def writes(self) -> list[dict]:
        return [r for r in self.rows() if r.get("phase") == "begin"]


class WriteFailed(Exception):
    pass


def journaled_write(journal: Journal, *, kind: str, target: str, read: Callable[[], Any],
                    send: Callable[[], None], intended: Any, expect: Any, dry_run: bool,
                    at: float, undo_of: str | None = None) -> str:
    """Do one write through the journal and return its journal id.

    `read` must succeed first: a failed read raises and nothing is written. After the send,
    the state is read back. When it differs from `expect`, the entry ends as failed.
    """
    before = read()
    jid = journal.begin(kind, target, before, intended, dry_run, at, undo_of)
    store.crashpoint("journal:after_fsync")
    if dry_run:
        journal.end(jid, ok=True, after=None, error="dry run: not sent")
        return jid
    try:
        send()
        after = read()
    except Exception as e:
        journal.end(jid, ok=False, error=repr(e))
        raise WriteFailed(f"{jid}: {e!r}") from e
    if expect is not None and after != expect:
        journal.end(jid, ok=False, after=after, error="read back differs from intent")
        raise WriteFailed(f"{jid}: read back {after!r}, expected {expect!r}")
    journal.end(jid, ok=True, after=after)
    return jid
