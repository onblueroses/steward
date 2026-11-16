# simple-autonomous-agent

[![CI](https://github.com/onblueroses/simple-autonomous-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/onblueroses/simple-autonomous-agent/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/onblueroses/simple-autonomous-agent)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A small Python library with two entry points. `Agent` runs a bounded tool-calling loop on any
OpenAI-compatible model. `run_pipeline` runs a score-ground-reason-draft pipeline for text that
must not sound machine-written.

## What it gives you

- Two runtime dependencies: `openai` and `pyyaml`.
- Tools are plain Python functions. The agent makes the OpenAI tool spec from their type hints.
- The loop always stops: at a final answer, at `max_steps`, or when the model makes no progress.
- The pipeline sends each stage to a different model. A cheap model scores, a thinking model
  reasons and a writing model drafts.
- Each pipeline stage fails independently. If search fails, the draft still happens.

## Example

```python
from simple_agent import Agent, create_client

def lookup_constant(name: str) -> str:
    """Return a known mathematical or physical constant."""
    return {"pi": "3.14159", "speed_of_light": "2.998e8 m/s"}.get(name, "unknown")

client = create_client("https://openrouter.ai/api/v1", "your-key")
agent = Agent(client=client, model="google/gemma-4-31b-it:free",
              tools=[lookup_constant], max_steps=6)

result = agent.run("What is the speed of light?")
print(result.output, result.steps, result.terminated)   # terminated is "done", "max_steps" or "no_progress"
```

`AsyncAgent` has the same constructor and runs with `await agent.arun(task)`.

## Components

| Module | What it does |
|---|---|
| `agent.py` | `Agent`, `AsyncAgent`, `AgentResult`, `tool_spec`: the tool loop |
| `pipeline.py` | `run_pipeline`, `run_batch` and their async versions |
| `llm.py` | `create_client`, `score`, `reason`, `draft`, with retry and backoff |
| `persona.py` | Loads YAML personas and writes them as an identity in the system prompt |
| `quality.py` | 14 default regex rules for AI writing patterns, opt-in German rules, `sanitize_input` |
| `state.py` | SQLite store for deduplication, drafts and run logs |
| `versioning.py` | `compute_prompt_hash`: a 16-character SHA-256 prefix of a prompt |
| `config.py` | Dataclasses. No environment variables and no global state |

Tool types, termination, memory, retries, personas, custom rules and the pipeline diagram:
[docs/details.md](docs/details.md).

## Install and run

You need Python 3.11 or newer.

```bash
git clone https://github.com/onblueroses/simple-autonomous-agent.git
cd simple-autonomous-agent
uv venv && source .venv/bin/activate
uv pip install -e .[dev]
python -m pytest tests/ -v
```

The tests need no API keys. All LLM calls are mocked. To run the examples against a real model,
get a key at openrouter.ai/keys:

```bash
export OPENROUTER_API_KEY="<your-openrouter-key>"
python examples/agent_real_api.py        # Agent loop with calculator + constants
python examples/real_api.py              # full content pipeline
python examples/async_real_api.py        # async pipeline, 3 items concurrently
```

## Layout

```text
simple_agent/   library code
personas/       example YAML personas
examples/       scripts that call a real API
tests/          tests with mocked LLM calls
scripts/        check_dedup.py, a gate for sync/async code pairs
docs/           details for readers
```

## Contributing and license

Read [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md). The license is [Apache 2.0](LICENSE).
