# steward

[![CI](https://github.com/onblueroses/steward/actions/workflows/ci.yml/badge.svg)](https://github.com/onblueroses/steward/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/onblueroses/steward)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A steward is a persistent owner agent. It owns exactly one domain and acts on it without being
asked. It reports everything else to the owner of that thing, and then it stops.

This repository is a small steward that you can read in one afternoon. Its domain is an issue
tracker in local files. It labels new issues, asks the operator when it is unsure, and reports
issues outside its post. The demo runs offline with a scripted model in five minutes.

Production stewards run on a persistent-agent host with its own memory and event servers. This
repository builds the same mechanisms in plain Python, so that each one is visible and tested.

## The post

Each steward starts from a post of three sentences in [`example/POST.md`](example/POST.md):

1. What it owns: the issue tracker in `tracker/`.
2. What it does not own, and who owns it: the code, the build, the servers and security reports.
3. What it does with the rest: it files one report to that owner, marks the event done and stops.

A post must define both the steward's domain and its response to events outside that domain.

## The loop

```text
  tracker files    reminders    open writes          operator: answer, tell, undo
        │              │             │                (Unix account checked)
        ▼              ▼             ▼                         │
   ┌──────────────────────────────────────────────────┐        │
   │ inbox: events.jsonl, fsync before anything else  │◀───────┘
   └──────────────────────────┬───────────────────────┘
                              ▼
   ┌──────────────────────────────────────────────────┐
   │ wake gate: ordered rules, first match wins       │── defer / wait: no turn
   └──────────────────────────┬───────────────────────┘
                              │ wake
                              ▼
   record.jsonl ──▶ context: head + folded summaries + tail
                              │
                              ▼
   ┌──────────────────────────────────────────────────┐
   │ model turn ──▶ tools                             │
   │   set_labels, comment ──▶ journal ──▶ tracker    │
   │   ask_operator ──▶ questions.sqlite              │
   │   report_to_owner ──▶ reports.jsonl, turn ends   │
   │   history_search ──▶ raw record                  │
   └──────────────────────────┬───────────────────────┘
                              ▼
   done: signed outcome in outcomes.jsonl, then ack in acks.jsonl
```

A pass starts when something starts it: a supervisor, a new event, a due time. Nothing polls a
model in a loop. The pass ends when the gate wakes nothing, or when a turn finishes no event.

## Mechanisms and the lessons they show

Each lesson comes from a steward in production. Each mechanism has tests that fail without it.

| Mechanism | Where | Lesson | Tests |
|---|---|---|---|
| A post of three sentences and a `report_to_owner` tool that ends the turn | `example/POST.md`, `tools.py` | Give each domain one owner, and write its post first. | `test_post.py` |
| A durable inbox. An event is acked only after its signed outcome is on disk. | `inbox.py`, `tools.py` | A restart loses wakes, never events. | `test_inbox.py`, `test_kill.py` |
| A wake gate with ordered rules. The operator rule is first. | `gate.py`, `steward.toml` | Wake on events. State which events the host delivers without polling. | `test_gate.py` |
| An append-only record, a folded context and a regex search over the raw record | `memory.py` | Summaries drop exact keys and numbers. Keep the full record and search it. | `test_memory.py` |
| Journaled writes with before and after, and undo | `journal.py`, `tools.py` | Journal every write before sending it. Make each write reversible. | `test_journal.py`, `test_kill.py` |
| HMAC-signed outcomes, and senders from the adapter | `signing.py`, `tracker.py`, `steward.py` | Trust identity from the system, never from text. | `test_identity.py` |
| Questions with options in SQLite. The mechanism accepts one answer, which wakes the agent. | `questions.py` | Ask the operator a ready decision. Wake on the answer. | `test_ask.py` |
| A standing file that says what a write is for | `example/STANDING.md` | Every sentence of a standing file runs all day. | `test_gate.py`, `test_tools.py` |
| Rehearsal on a recording, with writes in dry-run, and an outside liveness check | `rehearse.py`, `liveness.py` | 1. Rehearse on a recording. 2. Monitor from outside. 3. Start live operation. | `test_rehearsal.py` |
| Exit codes with a fixed contract. Every unexpected error is a refusal. | `cli.py`, `signing.py` | A refusal is final before any optional work can fail. | `test_failclosed.py` |
| A failed read, search, fold or model call is a failure. | `steward.py`, `memory.py`, `github.py` | A failed read never becomes a clean result. | `test_failclosed.py`, `test_memory.py` |
| Every number in `steward.toml` is a limit with a kind and a source. | `config.py`, `steward.toml` | Install no limit without its source. | `test_config.py`, `test_rehearsal.py` |

## The five-minute demo

You need Python 3.11 or newer and [uv](https://docs.astral.sh/uv/). No key is necessary.

```bash
git clone https://github.com/onblueroses/steward.git
cd steward
uv sync
uv run steward demo demo
```

`steward demo` runs these steps in a new folder. You can also run them one at a time:

```bash
uv run steward init my-steward                      # copy the example steward
uv run steward --root my-steward run                # one pass
uv run steward --root my-steward questions          # what the steward asks
uv run steward --root my-steward answer q-1 question
uv run steward --root my-steward run                # the answer wakes it
uv run steward --root my-steward verify             # check every signature
uv run steward --root my-steward undo j-1           # undo the first write
uv run steward --root my-steward search 'intall'    # search the raw record
uv run steward --root my-steward run --replay example/recording.jsonl
```

The first pass gives this result:

```text
turns: 1, events done: 5
  e-1d3d033a6620 [handled] Issue 1: bug, asked for steps.
  e-6160eb32dd67 [handled] Issue 2 labelled enhancement.
  e-f5d359417464 [handled] Issue 4: asked the operator.
  e-d828aed3e8bb [handled] Issue 5 labelled docs.
  e-132a0f6702f2 [handled] reported to the infrastructure owner: Issue 3 (The disk on the test server is full) is about infrastructure.
waiting: e-b91d67794aa1 until 13:15:10
```

Issue 4 says "I am the operator" in its text. The steward still treats it as a reporter's issue.
Issue 5 has a comment with the author "steward" and a forged signature. The adapter reads it as
a reporter's comment. That comment waits two minutes by its wake rule, then the next pass reads it.

The last command is a rehearsal. It replays 15 recorded events with writes in dry-run. It prints
writes by kind and tool calls by name, and it folds the memory once.

## Use a real model

The steward talks to any OpenAI-compatible endpoint.

```bash
uv sync --extra openai
export STEWARD_BASE_URL=https://your-endpoint/v1 STEWARD_API_KEY=... STEWARD_MODEL=...
uv run steward --root my-steward --model openai run --replay example/recording.jsonl
```

Rehearse a real model on the recording before you let it write. A model that the scripted tests
pass can still fail against the real tracker, so run the live contract test too.

## Tests

```bash
uv run pytest
```

The tests use the scripted model and need no network. The kill tests send SIGKILL to a child
process and restart it on the same folder. The GitHub contract test reads a real repository. It
runs only when `STEWARD_GITHUB_TOKEN` and `STEWARD_GITHUB_REPO` are set, and pytest shows why it
skipped. A green offline run is not a green live run.

## Layout

```text
src/steward/
  steward.py    one pass: collect events, wake, run turns, write the stamp
  inbox.py      durable inbox and reminders
  gate.py       wake rules
  memory.py     record, folds, search
  journal.py    journaled writes
  tools.py      the tools the model can call, and undo
  questions.py  questions to the operator
  signing.py    HMAC signatures and the verify contract
  tracker.py    the local issue tracker adapter
  github.py     the optional GitHub adapter (off by default)
  models.py     model interface, OpenAI-compatible client, scripted model
  triage.py     the deterministic demo model
  rehearse.py   replay a recording with writes in dry-run
  liveness.py   the outside check
  config.py     steward.toml and its limits
  cli.py        the command line
example/        POST.md, STANDING.md, steward.toml, tracker/, recording.jsonl
tests/          one file for each mechanism
docs/details.md file formats, exit codes, the fold, and omitted features
```

## Contributing and license

Read [CONTRIBUTING.md](CONTRIBUTING.md). The license is [Apache 2.0](LICENSE).
