"""The durable inbox: an event is on disk before anything acts on it, and stays pending until done.

Two append-only files. `events.jsonl` holds every event. `acks.jsonl` holds one row for each
event that the agent finished, written only after the outcome is on disk. A restart reads both
files again, so it can lose a wake but never an event.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import store


@dataclass(frozen=True)
class Event:
    id: str
    kind: str      # issue_opened, issue_comment, answer, reminder, open_write, message
    source: str    # the adapter that made the event
    sender: str    # identity as the adapter saw it: a file owner, an API user, a Unix account
    role: str      # "operator" or "reporter", set by the adapter, never read from text
    at: float
    data: dict = field(default_factory=dict)

    def to_row(self) -> dict:
        return asdict(self)


def make_event(kind: str, source: str, sender: str, role: str, at: float, data: dict,
               key: object | None = None) -> Event:
    """Build an event. Its id is a digest of `key`, so the same fact gives the same id."""
    ident = key if key is not None else {"kind": kind, "source": source, "data": data}
    return Event("e-" + store.digest(ident)[:12], kind, source, sender, role, at, data)


class Inbox:
    def __init__(self, directory: Path):
        self.events_path = directory / "events.jsonl"
        self.acks_path = directory / "acks.jsonl"

    def _events(self) -> store.Rows:
        return store.read(self.events_path)

    def put(self, event: Event) -> bool:
        """Store an event. Returns False when an event with this id is already stored."""
        if any(row.get("id") == event.id for row in self._events().rows):
            return False
        store.append(self.events_path, event.to_row())
        return True

    def ack(self, event_id: str, outcome_sig: str) -> None:
        store.append(self.acks_path, {"id": event_id, "outcome_sig": outcome_sig})

    def acked(self) -> set[str]:
        return {row["id"] for row in store.read(self.acks_path).rows if "id" in row}

    def pending(self) -> list[Event]:
        done = self.acked()
        out = []
        for row in self._events().rows:
            if row.get("id") in done:
                continue
            out.append(Event(**{k: row[k] for k in Event.__dataclass_fields__ if k in row}))
        return out

    def get(self, event_id: str) -> Event | None:
        for row in self._events().rows:
            if row.get("id") == event_id:
                return Event(**{k: row[k] for k in Event.__dataclass_fields__ if k in row})
        return None

    def bad_lines(self) -> dict[str, list[int]]:
        """Lines that did not parse. A torn last line after a kill shows up here."""
        out = {}
        for path in (self.events_path, self.acks_path):
            bad = store.read(path).bad_lines
            if bad:
                out[path.name] = bad
        return out


class Reminders:
    """Timed reminders the agent sets for itself. They are on disk, so a restart keeps them."""

    def __init__(self, path: Path):
        self.path = path

    def add(self, due: float, note: str, now: float) -> str:
        rid = "r-" + store.digest({"due": due, "note": note, "at": now})[:8]
        store.append(self.path, {"id": rid, "due": due, "note": note, "set_at": now})
        return rid

    def due(self, now: float) -> list[dict]:
        return [row for row in store.read(self.path).rows if row.get("due", now + 1) <= now]
