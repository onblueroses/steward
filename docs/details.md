# simple-autonomous-agent in detail

This file holds the full description of the `Agent`, the content pipeline and their options. The
short overview is in the [README](../README.md).

## Agent quick start

```python
from simple_agent import Agent, create_client

def calculate(expression: str) -> str:
    """Evaluate a numeric arithmetic expression."""
    import ast, operator
    ops = {ast.Add: operator.add, ast.Sub: operator.sub,
           ast.Mult: operator.mul, ast.Div: operator.truediv}
    def _eval(node):
        if isinstance(node, ast.Constant): return node.value
        if isinstance(node, ast.BinOp): return ops[type(node.op)](_eval(node.left), _eval(node.right))
        raise ValueError("disallowed")
    return str(_eval(ast.parse(expression, mode="eval").body))

def lookup_constant(name: str) -> str:
    """Return a known mathematical or physical constant."""
    return {"pi": "3.14159", "speed_of_light": "2.998e8 m/s"}.get(name, "unknown")

client = create_client("https://openrouter.ai/api/v1", "your-key")
agent = Agent(
    client=client,
    model="google/gemma-4-31b-it:free",
    tools=[calculate, lookup_constant],
    system_prompt="Use tools when you need to compute or look up values.",
    max_steps=6,
)

result = agent.run("What is pi times 1000?")
print(result.output)        # "3141.59"
print(result.steps)         # 2
print(result.terminated)    # "done"
```

Tools are plain functions with type hints. The agent makes the OpenAI tool spec from them. It
sends each tool call to its function and gives the result back to the model. It repeats this until
the model gives a final answer or the loop reaches `max_steps`. There is no decorator, no registry
and no plugin system.

## Why this library and not a larger framework

LangChain, CrewAI and AutoGen give an orchestration layer with many abstractions, plugin systems
and hundreds of transitive dependencies. The newer small frameworks (smolagents, OpenAI Agents
SDK) are closer to this library. They still bring their own runtime parts: tool registries, handoff
primitives and tracing infrastructure. This library has two entry points and nothing else:

- An `Agent` for tool-calling loops.
- A `run_pipeline` for score-ground-reason-draft content workflows.

Its properties:

- **Two runtime dependencies** (`openai`, `pyyaml`). There is no dependency tree to audit.
- **Two small entry points.** `Agent` is a bounded tool loop. `run_pipeline` is a multi-model
  content pipeline. You can use one, the other or both. They do not depend on each other.
- **Multi-model routing.** Cheap models score, thinking models reason and writing models draft.
  You do not pay the price of a large model to decide if an item is worth processing.
- **Output checks.** Regex rules find AI writing patterns before the output leaves the pipeline.
  Examples are em dashes, the `delve/crucial/landscape/showcase/tapestry` vocabulary, vague
  attributions, negative parallelism, knowledge-cutoff disclaimers and prompt-leak fragments.
- **Each stage fails independently.** If your search API is down, the draft still happens without
  grounding. A failure does not stop the next stages.
- **Cost-gated grounding.** An optional `needs_search` predicate runs before `ground_fn`. You do not
  pay for a search call when the question does not need new facts.
- **Prompt versioning.** `compute_prompt_hash(text)` returns a 16-character SHA-256 prefix. You can
  keep it next to the prompt.

## The Agent

### Tools

Tools are callables since v0.3.0. The agent reads the signature and type hints of each function
and makes the OpenAI tool spec from them:

```python
def search(query: str, limit: int = 5) -> str: ...
def lookup(name: str | None = None) -> str: ...

agent = Agent(client=client, model="...", tools=[search, lookup])
```

Supported parameter types are `str`, `int`, `float`, `bool`, `list[T]`, `Optional[T]` and
`T | None`. A parameter is required only if it has no default value and its annotation is not
optional. A parameter without a type, or with an unsupported type, raises `TypeError` when you
make the agent.

Raw dict tool specs are deferred to a later release.

### Termination

`AgentResult.terminated` is one of three strings:

- `"done"`: the model returned content with no tool calls. `result.output` is that content.
- `"max_steps"`: the loop used all its steps. `result.output` is the last model content. It can be
  empty.
- `"no_progress"`: the model returned no content and no tool calls. `result.output` is `""`.

`result.steps` counts model calls. One assistant response is one step, also when it contains many
parallel tool calls.

### Memory

The caller owns the memory. Give a list, and the agent adds messages to that list in place. The
same list comes back as `result.messages`:

```python
mem: list[dict] = []
agent.run("first question", memory=mem)
agent.run("follow-up", memory=mem)       # same conversation
```

If `system_prompt` is set, the agent puts the system message at index 0. It does this only when the
memory is empty or does not start with a system message. Repeated runs on the same memory do not
add a second copy.

### Tool errors are observations

An exception in a tool does not stop the agent. The agent writes the exception into a tool message
(`"Error: TypeError('bad arg')"`). The model can read it and try again in the remaining steps. If a
tool exception must stop the agent, catch it inside the tool and raise it again outside the agent.

### Retries

LLM calls retry on `RateLimitError`, `APITimeoutError` and `APIConnectionError` with exponential
backoff. The defaults are 2 retries and a base delay of 1.0 s. Set `max_retries=` and
`retry_base_delay=` on the `Agent` constructor to change them.

## Anti-patterns

- **Do not send the same request to N models in parallel and pick the best.** This uses many tokens
  and gives unclear results. Use the score-then-route pattern.
- **Do not run tool loops without a step limit.** Agents that do not stop are a real problem.
  `Agent` requires `max_steps >= 1` at construction. Increase it only with a reason.
- **Do not replace dependencies with stubs that only raise.** `import openai` already raises a clear
  error when openai is not installed. The same applies to your library: use declared dependencies
  and do not hide missing ones.
- **Do not write defensive code for cases that cannot happen.** Validate at system boundaries (user
  input, network responses). Trust types inside the code.

## Async

`AsyncAgent` has the same constructor as `Agent` and runs with `await agent.arun(task)`. Tools can
be `async def`, a `functools.partial` around an async function, or sync functions. The agent finds
awaitable results and awaits them. The sync `Agent` rejects awaitable tool results.

```python
from simple_agent import AsyncAgent, acreate_client

client = acreate_client("https://openrouter.ai/api/v1", "your-key")

async def fetch_price(symbol: str) -> str:
    """Look up a current stock price."""
    ...  # your async HTTP call

agent = AsyncAgent(client=client, model="...", tools=[fetch_price])
result = await agent.arun("What's TSLA trading at?")
```

The content pipeline also has async versions: `arun_pipeline`, `arun_batch`, `ascore`, `areason`,
`adraft` and `acreate_client`. `arun_batch` uses `asyncio.Semaphore` to limit concurrency, not
`time.sleep`.

## The content pipeline

Use the pipeline when you need score-ground-reason-draft-validate-persist and not an open tool
loop:

```text
Input item
    |
    v
[Score]          cheap model decides if the item is worth processing
    |
    v
[Select persona] picks the right voice for the content
    |
    v
[Ground]         calls your search function for real-world context (optional)
    |
    v
[Reason]         thinking model analyzes the content + grounding
    |
    v
[Draft]          writing model generates output in persona voice
    |
    v
[Validate]       regex rules catch AI writing patterns in the output
    |
    v
[Persist]        SQLite state store for deduplication and run logs (optional)
```

Each stage fails independently. If search is down, the draft still happens without grounding
context. If the reasoning model times out, the writer works without the analysis.

### Pipeline quick start

```python
from simple_agent import (
    ModelConfig, PipelineConfig, create_client,
    load_persona, run_pipeline,
)

client = create_client("https://openrouter.ai/api/v1", "your-key")

config = PipelineConfig(
    scorer=ModelConfig("nvidia/nemotron-nano-9b-v2:free", max_tokens=256),
    reasoner=ModelConfig("deepseek/deepseek-v4-flash:free", max_tokens=1024),
    writer=ModelConfig("google/gemma-4-31b-it:free", max_tokens=1024),
    scorer_client=client,
    writer_client=client,
    score_threshold=0.6,
)

persona = load_persona("personas/analyst.yaml")
item = {"id": "1", "text": "How do open-source dependency audit tools compare on signal-to-noise?"}

result = run_pipeline(item, config, personas=[persona])
print(result.draft)
```

The pipeline uses three models for three jobs. A small model scores (pennies per thousand calls). A
long-context model analyzes. A stronger model writes the draft. The model IDs are OpenRouter free
tier as of 2026-05-15. You can use any OpenAI-compatible provider.

### Modules

- **`llm.py`**: `create_client()`, `score()`, `reason()`, `draft()` and their async versions.
  `score()` does cheap classification. `reason()` handles thinking models that return output in
  `reasoning`, `reasoning_content` or `<think>` tags. `draft()` gives the persona voice through an
  identity in the system message. All calls retry with exponential backoff on rate limits and
  timeouts.
- **`pipeline.py`**: `run_pipeline()` connects the stages and puts a try/except around each one.
  The JSON extraction handles LLM responses inside markdown fences or after preamble text.
  `run_batch()` adds rate limits and run logs. The async versions do the same with a semaphore
  that limits concurrency.
- **`agent.py`**: `Agent`, `AsyncAgent`, `AgentResult` and `tool_spec`. A bounded tool-calling loop
  with schemas made from type hints. It does not depend on the pipeline. The agent works with any
  model and any callable tools.
- **`persona.py`**: loads YAML persona configs. `build_system_prompt()` gives the persona as an
  identity ("You are Marcus Voss...") and not as an instruction. The identity form gives a more
  consistent voice.
- **`quality.py`**: default regex rules for AI writing patterns, and opt-in German rules
  (`default_rules_de()`). `sanitize_input()` removes prompt injection from untrusted text. The
  defaults are a starting point. Tune them for your domain.
- **`state.py`**: a SQLite wrapper with three tables: items (deduplication), drafts (lifecycle) and
  runs (logs). It works as a context manager, so connections close correctly.
- **`versioning.py`**: `compute_prompt_hash(text)` returns a 16-character SHA-256 prefix for
  replay determinism.
- **`config.py`**: dataclasses. No environment variables and no global state.

### Personas

```yaml
name: "Your Persona Name"
identity: >
  Background and perspective, written as if describing who this
  person IS rather than what they should do.

voice: >
  How they communicate. Sentence structure, formality, patterns.

expertise:
  - Domain 1
  - Domain 2

constraints:
  - Style rules specific to this persona

example_outputs:
  - >
    A sample of how this persona writes.
```

### Cost-gated grounding

`ground_fn` calls can be the most expensive step. Skip them when the question does not need new
data:

```python
def needs_search(item, persona, config) -> bool:
    return "today" in item["text"].lower() or "current" in item["text"].lower()

config = PipelineConfig(..., ground_fn=tavily_search, needs_search=needs_search)
```

The async config accepts a sync predicate or a predicate that returns an awaitable.

### Custom quality rules

```python
from simple_agent import QualityRule, default_rules

my_rules = default_rules() + [
    QualityRule(name="no_jargon", pattern=r"\b(?:synergize|leverage|ideate)\b",
                description="Corporate jargon."),
]

config = PipelineConfig(..., quality_rules=my_rules)
```

Rules on a persona **replace** the pipeline defaults for the drafts of that persona:

```python
persona.quality_rules = [
    QualityRule(name="no_em_dash", pattern=r"\u2014", description="In-house style."),
]
```

### Prompt versioning

```python
from simple_agent import compute_prompt_hash

PROMPT_V1 = "Classify the sentiment of this content: ..."
PROMPT_V1_HASH = compute_prompt_hash(PROMPT_V1)  # 16-char SHA-256 prefix
```

Keep a hash next to the prompt. Then replays of stored prompt logs can verify themselves.
