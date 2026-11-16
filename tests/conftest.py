from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from steward import Steward
from steward.models import Reply, ToolCall

REPO = Path(__file__).resolve().parents[1]
EXAMPLE = REPO / "example"


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """A fresh copy of the example steward with its five issues."""
    target = tmp_path / "steward"
    shutil.copytree(EXAMPLE, target, ignore=shutil.ignore_patterns("recording.jsonl", "state"))
    return target


def only_issues(root: Path, *keep: str) -> None:
    for path in (root / "tracker" / "issues").glob("*.json"):
        if path.stem not in keep:
            path.unlink()


def set_limit(root: Path, name: str, value: float) -> None:
    """Change the value of one limit in the copied steward.toml."""
    lines = (root / "steward.toml").read_text().splitlines()
    out, inside = [], False
    for line in lines:
        if line.startswith("["):
            inside = line.strip() == f"[limits.{name}]"
        if inside and line.startswith("value ="):
            line = f"value = {value}"
        out.append(line)
    (root / "steward.toml").write_text("\n".join(out) + "\n")


def call(name: str, **arguments) -> ToolCall:
    call.n = getattr(call, "n", 0) + 1
    return ToolCall(f"t-{call.n}", name, arguments)


def reply(*calls: ToolCall, content: str = "") -> Reply:
    return Reply(content, list(calls))


def labels(root: Path, issue_id: str) -> list[str]:
    return json.loads((root / "tracker" / "issues" / f"{issue_id}.json").read_text())["labels"]


def event_for(steward: Steward, issue_id: str, kind: str = "issue_opened"):
    for e in steward.inbox.pending():
        if e.kind == kind and e.data.get("issue_id") == issue_id:
            return e
    raise AssertionError(f"no pending {kind} event for issue {issue_id}")


def run_cli(root: Path, *args: str, env: dict | None = None, wait: bool = True):
    """Run the CLI in a child process, as a supervisor would."""
    full_env = {**os.environ, **(env or {})}
    cmd = [sys.executable, "-m", "steward.cli", "--root", str(root), *args]
    if not wait:
        return subprocess.Popen(cmd, env=full_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return subprocess.run(cmd, env=full_env, capture_output=True, text=True)


def write_script(path: Path, steps: list) -> Path:
    path.write_text(json.dumps(steps))
    return path
