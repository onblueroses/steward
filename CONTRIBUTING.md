# Contributing

This repository is small on purpose. A reader must understand it in one afternoon. Keep it so.

## Installation and tests

```bash
uv sync
uv run pytest
```

The tests need no key and no network. The live GitHub test runs only with credentials.

## Rules for a change

- A new mechanism comes with a test that fails without it. Add a control beside each failure case.
- A failed read, call or search must stay a failure. Never turn it into an empty result.
- A new number in `steward.toml` goes into `[limits]` with its kind and its source. The loader refuses it otherwise.
- Do not add a delete tool. Correct a write with a further write.
- Tool docstrings, error messages and the standing file are read by a model. Write them in short,
  active sentences without semicolons.
- No new runtime dependency without an issue first. The core has none. `openai` is an optional extra.
- Comments explain why. The reader knows Python.
- Commit subjects are short, capitalized and in the imperative.
