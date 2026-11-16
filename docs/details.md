# Details

This page gives the depth behind the README: the files, the order of each operation, the exit
codes, and the omitted features.

## The state folder

Every file in `state/` is append-only JSONL, except the SQLite question store, the key and the stamp.

| File | What it holds | Written by |
|---|---|---|
| `inbox/events.jsonl` | every event, once, keyed by a digest | adapters, the operator door, `collect` |
| `inbox/acks.jsonl` | one row for each finished event, with its outcome signature | `done`, `report_to_owner` |
| `record.jsonl` | every event, model reply, tool call, result and failure | each turn |
| `folds.jsonl` | summaries of old stretches of the record | the context compiler |
| `journal.jsonl` | a `begin` and an `end` row for each write | `journaled_write` |
| `outcomes.jsonl` | signed outcomes | `done` |
| `reports.jsonl` | signed reports to other owners | `report_to_owner` |
| `reminders.jsonl` | reminders that the agent set | `remind` |
| `questions.sqlite` | questions, options, answers | `ask_operator`, `steward answer` |
| `key` | the HMAC key, mode 0600 | the first start |
| `stamp.json` | the result of the last pass | the end of each pass |

`store.append` writes one row and calls `fsync` before it returns. A kill during an append can
leave a torn last line. The next append starts a new line first, so the fragment does not
swallow the next row. A line that does not parse is reported as a failure of the pass. It is
never dropped in silence.

## One pass

1. `collect` puts every new fact into the inbox: new issues and comments, due reminders,
   answered questions, and open journal entries.
2. The wake gate sorts every pending event into `wake`, `waiting` or `deferred`.
3. When nothing wakes, the pass writes the stamp and ends.
4. A turn offers the woken and the deferred events. A waiting event is not offered before its
   due time.
5. The model calls tools until it returns a reply without a tool call, or until
   `report_to_owner` or the fuse ends the turn.
6. The pass runs another turn while events wake and the last turn finished at least one event.

The turn message carries every offered event, the ids that woke the turn, the waiting events and
the open questions. The standing file says that these arrive without a call. No tool lists events.

## Acknowledge an event

`done(event_id, outcome)` does three things in this order:

1. It builds the outcome row: event, outcome text, status, failures of the turn, turn number.
2. It signs the row and appends it to `outcomes.jsonl`.
3. It appends the ack to `acks.jsonl`.

A kill between step 2 and step 3 offers the event again at the next start. Delivery is therefore
at least once. `test_kill.py` shows the outcome twice and the ack once.

The status is `failed` when any tool call of the turn failed. The failures go into the outcome.
So a failed read can never produce a clean outcome.

## Order of a write

`journaled_write` reads the state before and appends a `begin` row with the intent. Then it sends
the write, reads the state back and appends an `end` row. Label writes also compare the read-back with the
intent. A difference ends the entry as failed.

A kill after the `begin` row leaves an open entry. At the next start it becomes an `open_write`
event with the note "Write begun, outcome unknown". The agent reads the target and marks the event
done. Its outcome closes the entry as "outcome unknown", so undo refuses it.

Undo of a label write sets the old labels again, through the journal. It refuses when the labels
changed after the write. Undo of a comment adds a correction comment. Nothing is deleted.

## Identity

The adapter sets `sender` and `role` on each event. The local tracker takes the sender from the
owner of the issue file. Every issue and comment gets the role `reporter`. The GitHub adapter
takes the sender from `user.login`, and gives `operator` only to logins in `operator_logins`.

The operator door is the command line. `answer`, `tell` and `undo` check the Unix account that
runs them against `operator_uids`. Text inside an event never changes its role.

The steward signs its own comments. The adapter skips a comment as the steward's own only when its
signature verifies. A comment with the author "steward" and a bad signature is a reporter's comment.

## Memory

The context of a turn has three parts:

- The head: the start of the first turn, verbatim.
- The folds: summaries, in order.
- The tail: every entry after the last fold, verbatim.

When the parts are larger than `context_budget_tokens`, the compiler folds the oldest entries
of the tail into one summary. One fold takes up to `fold_chunk_tokens`. The newest turn is never
folded. With no budget in the config, nothing folds. A failed fold call is a failure of the turn,
and the events stay pending.

Token counts are an estimate of four characters for each token. The estimate only sizes the view.

`history_search(pattern)` searches the raw record with a Python regular expression. A bad pattern
is a failure. It never reads as "no match".

## Exit codes

| Code | Meaning |
|---|---|
| 0 | The command succeeded. `verify`: every row has a valid signature. |
| 1 | The pass ran and recorded a failure. `liveness`: the last pass failed. |
| 3 | Refused or unusable: bad config, unknown account, missing key, bad pattern, any unexpected error. |
| 10 | `verify` found rows without a valid signature. |

The CLI catches every exception and returns 3. A crash can therefore never look like a pass.

## Limits

`config.load` refuses a number outside `[limits]` and a limit without `kind` and `source`. The
kinds are `system` (a limit of the system itself), `measured`, `ruling` and `guard`.

| Limit | Kind | Why |
|---|---|---|
| `tool_call_fuse` | guard | Stops a runaway turn. It is twice the busiest measured turn. A test ties the value to the rehearsal. |
| `comment_settle_seconds` | ruling | Lets a burst of comments end before one turn reads them. |
| `context_budget_tokens` | ruling | Small in the demo, so that a fold happens in the rehearsal. |
| `fold_chunk_tokens` | ruling | Half of the demo budget. A fold has an almost fixed price, so measure it on your model before you change it. |

Some values are absent on purpose. The model client keeps its own default timeout and retries.
`liveness` has no age limit until you measure your feed and add `liveness_pending_age_seconds`.
Searches and lists have no caps.

## Rehearsal

`steward run --replay FILE` makes a folder under `rehearsals/`. It copies the three steward files
and builds a read-only tracker from the issue snapshots in the recording. It has no adapter to a
live tracker. Writes run in dry-run: they are journaled and not sent. A recording has the format of
`inbox/events.jsonl`, so the inbox of a live steward is also a recording.

Do not run a dry-run phase on a live feed. The agent would close events whose writes never landed.

## Omitted features

- **A long-running process.** `steward run` is one pass. A supervisor starts it, for example a
  systemd path unit on the tracker folder or a timer. A persistent-agent host does this in
  production.
- **Comments from GitHub.** The GitHub adapter reads issues, labels and comments, and writes
  labels and comments. It makes events for new issues only.
- **Push of questions to a phone.** Questions wait in SQLite until the operator runs
  `steward questions`. A notifier would read the same table.
- **Gated actions.** The tool set has no delete, close or send tool. Actions such as payments or
  messages in the operator's name stay outside the tool set.
- **A lint of the standing file.** The rehearsal prints counts for a person to read. It does not
  find a rule that the agent can settle only by a write. Read the counts and the comments.
- **Scale.** Each operation reads its JSONL file in full. That is simple and correct for a demo.
  A large inbox needs an index.
