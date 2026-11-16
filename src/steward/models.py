"""Models: one small interface, an OpenAI-compatible client, and a scripted model for tests.

A model takes chat messages and tool specs and returns a Reply. `purpose` is "turn" for a
working call and "fold" for a summary of old memory.
"""

from __future__ import annotations

import inspect
import json
import os
import time
import types
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class ModelError(Exception):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class Reply:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


class Model(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], purpose: str = "turn") -> Reply: ...


# --- tool specs from type hints -------------------------------------------------------------

_SCALARS = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _schema(annotation: Any, where: str) -> dict:
    if annotation in _SCALARS:
        return {"type": _SCALARS[annotation]}
    if typing.get_origin(annotation) is list:
        (item,) = typing.get_args(annotation) or (str,)
        if item in _SCALARS:
            return {"type": "array", "items": {"type": _SCALARS[item]}}
    if typing.get_origin(annotation) in (typing.Union, types.UnionType):
        raise TypeError(f"{where}: optional parameters are not supported; give a default")
    raise TypeError(f"{where}: type {annotation!r} is not supported")


def tool_spec(fn: Callable) -> dict:
    """An OpenAI tool spec built from a function's signature, type hints and docstring."""
    hints = typing.get_type_hints(fn)
    props, required = {}, []
    for name, param in inspect.signature(fn).parameters.items():
        if name not in hints:
            raise TypeError(f"{fn.__name__}.{name} has no type annotation")
        props[name] = _schema(hints[name], f"{fn.__name__}.{name}")
        if param.default is inspect.Parameter.empty:
            required.append(name)
    doc = inspect.getdoc(fn) or ""
    return {"type": "function", "function": {
        "name": fn.__name__, "description": doc,
        "parameters": {"type": "object", "properties": props, "required": required},
    }}


# --- OpenAI-compatible endpoint -------------------------------------------------------------

class OpenAIModel:
    """Any OpenAI-compatible chat endpoint. Configure it with environment variables:

    STEWARD_BASE_URL, STEWARD_API_KEY, STEWARD_MODEL.
    Timeouts and retries are the client library's own defaults. This code sets none.
    """

    def __init__(self, base_url: str, api_key: str, model: str):
        try:
            import openai
        except ImportError as e:
            raise ModelError("install the extra: uv sync --extra openai") from e
        self.client = openai.OpenAI(base_url=base_url, api_key=api_key)
        self.model = model

    @classmethod
    def from_env(cls) -> "OpenAIModel":
        missing = [k for k in ("STEWARD_BASE_URL", "STEWARD_API_KEY", "STEWARD_MODEL") if not os.environ.get(k)]
        if missing:
            raise ModelError("set " + ", ".join(missing))
        return cls(os.environ["STEWARD_BASE_URL"], os.environ["STEWARD_API_KEY"], os.environ["STEWARD_MODEL"])

    def complete(self, messages: list[dict], tools: list[dict], purpose: str = "turn") -> Reply:
        kwargs: dict = {"model": self.model, "messages": messages}
        if tools:
            kwargs["tools"] = tools
        try:
            resp = self.client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            calls = [ToolCall(c.id, c.function.name, json.loads(c.function.arguments or "{}"))
                     for c in (msg.tool_calls or [])]
        except Exception as e:  # every failure of the call or its parse is a model failure
            raise ModelError(f"model call failed: {e!r}") from e
        return Reply(msg.content or "", calls)


# --- scripted model for tests ----------------------------------------------------------------

class ScriptedModel:
    """Returns the next reply from a fixed list. It records every request it gets.

    A step can be a Reply, an Exception to raise, or the string "block", which writes
    `block_marker` and then sleeps, so that a test can kill the process mid-turn.
    """

    def __init__(self, steps: list, summary: str = "Older entries, folded.", block_marker: Path | None = None):
        self.steps = list(steps)
        self.summary = summary
        self.block_marker = block_marker
        self.requests: list[list[dict]] = []

    def complete(self, messages: list[dict], tools: list[dict], purpose: str = "turn") -> Reply:
        self.requests.append([dict(m) for m in messages])
        if purpose == "fold":
            return Reply(self.summary)
        if not self.steps:
            return Reply("Nothing more to do.")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        if step == "block":
            if self.block_marker:
                self.block_marker.write_text("blocked\n")
            while True:
                time.sleep(3600)
        return step

    @classmethod
    def from_file(cls, path: Path) -> "ScriptedModel":
        """Load steps from JSON: a list of {"content", "tool_calls": [{"name", "arguments"}]} or "block"."""
        steps: list = []
        for i, raw in enumerate(json.loads(path.read_text())):
            if raw == "block":
                steps.append("block")
                continue
            calls = [ToolCall(f"call-{i}-{j}", c["name"], c.get("arguments", {}))
                     for j, c in enumerate(raw.get("tool_calls", []))]
            steps.append(Reply(raw.get("content", ""), calls))
        marker = os.environ.get("STEWARD_BLOCK_MARKER")
        return cls(steps, block_marker=Path(marker) if marker else None)
