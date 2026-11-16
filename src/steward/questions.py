"""Questions to the operator: a ready decision with options, stored in SQLite.

One row for each question. It stays after the answer. An atomic update accepts one answer
while the question is open, and refuses every later one. The answer becomes an inbox event,
so it wakes the agent and is offered again after a restart until the agent marks it done.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA = """
create table if not exists questions (
  id text primary key,
  event_id text not null,
  question text not null,
  context text not null,
  options text not null,
  recommended text not null,
  status text not null check (status in ('open', 'answered')),
  answer text,
  answered_by text,
  asked_at real not null,
  answered_at real
)
"""


class QuestionError(Exception):
    pass


class Questions:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute(SCHEMA)

    def ask(self, event_id: str, question: str, context: str, options: list[str],
            recommended: str, now: float) -> str:
        if len(options) < 2 or len(set(options)) != len(options):
            raise QuestionError("give two or more different options")
        if recommended not in options:
            raise QuestionError("the recommended option must be one of the options")
        if not question.strip() or not context.strip():
            raise QuestionError("a question needs its text and its context")
        number = self.db.execute("select count(*) from questions").fetchone()[0] + 1
        qid = f"q-{number}"
        self.db.execute(
            "insert into questions (id, event_id, question, context, options, recommended,"
            " status, asked_at) values (?, ?, ?, ?, ?, ?, 'open', ?)",
            (qid, event_id, question, context, json.dumps(options), recommended, now),
        )
        return qid

    def answer(self, qid: str, option: str, by: str, now: float) -> None:
        row = self.get(qid)
        if row is None:
            raise QuestionError(f"no question {qid}")
        if option not in row["options"]:
            raise QuestionError(f"{option!r} is not an option of {qid}: {row['options']}")
        cur = self.db.execute(
            "update questions set status = 'answered', answer = ?, answered_by = ?, answered_at = ?"
            " where id = ? and status = 'open'",
            (option, by, now, qid),
        )
        if cur.rowcount != 1:
            raise QuestionError(f"{qid} already has an answer")

    def get(self, qid: str) -> dict | None:
        row = self.db.execute("select * from questions where id = ?", (qid,)).fetchone()
        return self._row(row) if row else None

    def all(self, status: str | None = None) -> list[dict]:
        sql, args = "select * from questions", ()
        if status:
            sql, args = sql + " where status = ?", (status,)
        return [self._row(r) for r in self.db.execute(sql + " order by asked_at, id", args)]

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        out = dict(row)
        out["options"] = json.loads(out["options"])
        return out
