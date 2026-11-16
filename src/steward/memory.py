"""Memory: a permanent record on disk, and a folded view of it for each turn.

`record.jsonl` keeps every entry the agent saw or made. Nothing deletes from it. `folds.jsonl`
keeps summaries of old stretches of the record. Each turn reads a compiled context: the start of
the first turn verbatim (the head), the summaries, then the newest entries verbatim (the tail).

Summaries drop exact keys and numbers. So the agent can also search the raw record.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

from . import store

# Rough characters per token for English text. An estimate: it only sizes the compiled view.
CHARS_PER_TOKEN = 4


def tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN + 1


class SearchError(Exception):
    pass


class Record:
    def __init__(self, directory: Path):
        self.path = directory / "record.jsonl"
        self.folds_path = directory / "folds.jsonl"

    def entries(self) -> list[dict]:
        return store.read(self.path).rows

    def folds(self) -> list[dict]:
        return store.read(self.folds_path).rows

    def next_turn(self) -> int:
        return max((e.get("turn", 0) for e in self.entries()), default=0) + 1

    def add(self, turn: int, kind: str, body: dict, at: float) -> int:
        n = len(self.entries()) + 1
        store.append(self.path, {"n": n, "turn": turn, "kind": kind, "at": at, "body": body})
        return n

    @staticmethod
    def render(entry: dict) -> str:
        return f"[{entry['n']}] turn {entry['turn']} {entry['kind']}: {store.canonical(entry['body'])}"

    def search(self, pattern: str) -> list[str]:
        """Every raw entry that matches a regular expression.

        A bad pattern raises SearchError. It never reads as "no match".
        """
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise SearchError(f"bad pattern {pattern!r}: {e}") from e
        bad = store.read(self.path).bad_lines
        if bad:
            raise SearchError(f"record lines {bad} do not parse; the search would be partial")
        return [line for line in map(self.render, self.entries()) if rx.search(line)]

    def compile(self, budget: float | None, chunk: float | None,
                summarize: Callable[[str], str]) -> str:
        """Build the memory text for the next turn, and fold old entries when it is too long.

        With no budget in the config, nothing folds. The newest turn is never folded.
        `summarize` is a model call. When it fails, its error goes up and no fold is written.
        """
        entries = self.entries()
        if not entries:
            return "(no earlier turns)"
        first_turn = entries[0]["turn"]
        last_turn = entries[-1]["turn"]
        # The head is what the agent was given first: the first turn up to its turn_start.
        head = []
        for e in entries:
            if e["turn"] != first_turn:
                break
            head.append(e)
            if e["kind"] == "turn_start":
                break
        folds = self.folds()
        covered = max((f["last"] for f in folds), default=head[-1]["n"])
        rest = [e for e in entries if e["n"] > covered]

        def size() -> int:
            parts = [self.render(e) for e in head + rest] + [f["summary"] for f in folds]
            return sum(tokens(p) for p in parts)

        while budget is not None and size() > budget:
            foldable = [e for e in rest if e["turn"] != last_turn]
            if not foldable:
                break
            taken: list[dict] = []
            for e in foldable:
                if taken and chunk is not None and sum(tokens(self.render(t)) for t in taken) + tokens(self.render(e)) > chunk:
                    break
                taken.append(e)
            text = "\n".join(self.render(e) for e in taken)
            summary = summarize(text)
            fold = {"first": taken[0]["n"], "last": taken[-1]["n"], "summary": summary}
            store.append(self.folds_path, fold)
            folds.append(fold)
            rest = rest[len(taken):]

        lines = ["## Head (the start of your first turn, verbatim)"] + [self.render(e) for e in head]
        if folds:
            lines.append("## Folded (summaries; search the record for exact keys and numbers)")
            lines += [f"entries {f['first']} to {f['last']}: {f['summary']}" for f in folds]
        lines.append("## Tail (newest entries, verbatim)")
        lines += [self.render(e) for e in rest]
        return "\n".join(lines)
