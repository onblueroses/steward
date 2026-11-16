"""Append-only JSONL files that survive a kill, and crash points for kill tests."""

from __future__ import annotations

import hashlib
import json
import os
import signal
from dataclasses import dataclass
from pathlib import Path


def canonical(obj: object) -> str:
    """One JSON form for an object, so that digests and signatures are stable."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(obj: object) -> str:
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def append(path: Path, row: dict) -> None:
    """Append one row and fsync it. The row is on disk when this returns."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab+") as f:
        # A kill during an earlier append can leave a torn last line without a newline.
        # Start a new line so that the torn fragment does not swallow this row.
        if f.seek(0, os.SEEK_END) > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b"\n":
                f.write(b"\n")
        f.write((canonical(row) + "\n").encode())
        f.flush()
        os.fsync(f.fileno())


@dataclass
class Rows:
    rows: list[dict]
    bad_lines: list[int]  # line numbers that did not parse; the caller must report them


def read(path: Path) -> Rows:
    """Read every row. A line that does not parse is reported, never dropped in silence."""
    if not path.exists():
        return Rows([], [])
    rows: list[dict] = []
    bad: list[int] = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for number, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                bad.append(number)
                continue
            if isinstance(row, dict):
                rows.append(row)
            else:
                bad.append(number)
    return Rows(rows, bad)


def write_atomic(path: Path, text: str) -> None:
    """Replace a file so that a reader sees the old or the new content, never half."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def crashpoint(name: str) -> None:
    """Kill this process with SIGKILL when STEWARD_CRASH_AT names this point.

    The kill tests use it to stop the process at an exact place, with no cleanup.
    """
    if os.environ.get("STEWARD_CRASH_AT") == name:
        os.kill(os.getpid(), signal.SIGKILL)
