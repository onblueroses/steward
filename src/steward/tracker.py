"""The example domain: an issue tracker held in local JSON files.

`tracker/issues/<id>.json` holds one issue: id, title, body, labels, comments. The adapter
turns new issues and new comments into events. It takes the sender from the file owner, the
only identity the file system gives. A name written inside a file is data, never identity.
"""

from __future__ import annotations

import json
import pwd
import re
from pathlib import Path
from typing import Protocol

from . import store
from .inbox import Event, make_event
from .signing import Signer

ISSUE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
STEWARD_AUTHOR = "steward"


class TrackerError(Exception):
    pass


class Tracker(Protocol):
    def ingest(self, now: float) -> tuple[list[Event], list[str]]: ...
    def get(self, issue_id: str) -> dict: ...
    def labels(self, issue_id: str) -> list[str]: ...
    def comments(self, issue_id: str) -> list[dict]: ...
    def set_labels(self, issue_id: str, labels: list[str]) -> None: ...
    def add_comment(self, issue_id: str, comment: dict) -> None: ...


def _account(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid:{uid}"


class LocalTracker:
    source = "local"

    def __init__(self, root: Path, signer: Signer):
        self.dir = root / "issues"
        self.signer = signer

    def _path(self, issue_id: str) -> Path:
        if not ISSUE_ID.match(issue_id):
            raise TrackerError(f"bad issue id {issue_id!r}")
        return self.dir / f"{issue_id}.json"

    def _load(self, path: Path) -> dict:
        try:
            issue = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as e:
            raise TrackerError(f"no issue file {path.name}") from e
        except (OSError, json.JSONDecodeError) as e:
            raise TrackerError(f"cannot read {path.name}: {e}") from e
        if not isinstance(issue, dict) or "title" not in issue:
            raise TrackerError(f"{path.name} is not an issue")
        issue.setdefault("labels", [])
        issue.setdefault("comments", [])
        return issue

    def get(self, issue_id: str) -> dict:
        return self._load(self._path(issue_id))

    def labels(self, issue_id: str) -> list[str]:
        return list(self.get(issue_id)["labels"])

    def comments(self, issue_id: str) -> list[dict]:
        return list(self.get(issue_id)["comments"])

    def _save(self, issue_id: str, issue: dict) -> None:
        store.write_atomic(self._path(issue_id), json.dumps(issue, indent=2, ensure_ascii=False) + "\n")

    def set_labels(self, issue_id: str, labels: list[str]) -> None:
        issue = self.get(issue_id)
        issue["labels"] = labels
        self._save(issue_id, issue)

    def add_comment(self, issue_id: str, comment: dict) -> None:
        issue = self.get(issue_id)
        issue["comments"].append(comment)
        self._save(issue_id, issue)

    def is_own(self, issue_id: str, comment: dict) -> bool:
        """A comment is the steward's own only when its signature verifies."""
        body = {"issue": issue_id, "text": comment.get("text")}
        return comment.get("author") == STEWARD_AUTHOR and self.signer.verify(body, comment.get("sig"))

    def ingest(self, now: float) -> tuple[list[Event], list[str]]:
        """Events for every issue and every comment that is not the steward's own.

        Returns (events, failures). An unreadable file is a failure, never an empty result.
        """
        events: list[Event] = []
        failures: list[str] = []
        if not self.dir.is_dir():
            return [], [f"tracker folder {self.dir} is missing"]
        for path in sorted(self.dir.glob("*.json")):
            issue_id = path.stem
            try:
                issue = self._load(path)
                sender = _account(path.stat().st_uid)
            except (TrackerError, OSError) as e:
                failures.append(f"ingest {path.name}: {e}")
                continue
            snapshot = {"issue_id": issue_id, "title": issue["title"], "body": issue.get("body", "")}
            events.append(make_event("issue_opened", self.source, sender, "reporter", now, snapshot,
                                     key=("local", "opened", issue_id)))
            for i, comment in enumerate(issue["comments"]):
                if self.is_own(issue_id, comment):
                    continue
                data = {"issue_id": issue_id, "index": i, "text": comment.get("text", ""),
                        "claimed_author": comment.get("author")}
                events.append(make_event("issue_comment", self.source, sender, "reporter", now, data,
                                         key=("local", "comment", issue_id, i)))
        return events, failures


class SnapshotTracker:
    """A read-only tracker built from the issue snapshots in recorded events. Rehearsals use it.

    It has no adapter to the outside, so a rehearsal cannot reach a live tracker.
    """

    source = "recording"

    def __init__(self, events: list[Event]):
        self.issues: dict[str, dict] = {}
        for e in events:
            if e.kind == "issue_opened":
                d = e.data
                self.issues[d["issue_id"]] = {"id": d["issue_id"], "title": d["title"],
                                              "body": d.get("body", ""), "labels": [], "comments": []}

    def ingest(self, now: float) -> tuple[list[Event], list[str]]:
        return [], []

    def get(self, issue_id: str) -> dict:
        if issue_id not in self.issues:
            raise TrackerError(f"no issue {issue_id} in the recording")
        return json.loads(json.dumps(self.issues[issue_id]))

    def labels(self, issue_id: str) -> list[str]:
        return list(self.get(issue_id)["labels"])

    def comments(self, issue_id: str) -> list[dict]:
        return list(self.get(issue_id)["comments"])

    def set_labels(self, issue_id: str, labels: list[str]) -> None:
        raise TrackerError("a rehearsal sends no writes")

    def add_comment(self, issue_id: str, comment: dict) -> None:
        raise TrackerError("a rehearsal sends no writes")
