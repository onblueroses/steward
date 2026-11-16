"""Load steward.toml. Every number in it is a limit with a stated source, or it is refused."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# What can stand behind a limit. "guard" is a fuse against a runaway, named as such.
LIMIT_KINDS = {"system", "measured", "ruling", "guard"}

# Numbers that are identities, not limits.
NOT_LIMITS = {("identity", "operator_uids")}

WAKE_ACTIONS = {"wake", "wake_after", "defer"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Limit:
    name: str
    value: float
    kind: str
    source: str


@dataclass(frozen=True)
class WakeRule:
    index: int
    match: dict
    action: str
    after_limit: str | None = None


@dataclass
class Config:
    name: str
    state_dir: Path
    tracker_dir: Path
    operator_uids: list[int]
    owners: dict[str, str]
    labels: list[str]
    wake_rules: list[WakeRule]
    limits: dict[str, Limit]
    github: dict = field(default_factory=dict)

    def limit(self, name: str) -> float | None:
        """The value of a limit, or None when the config has no such limit."""
        found = self.limits.get(name)
        return found.value if found else None


def _numbers_outside_limits(node: object, path: tuple[str, ...]) -> list[str]:
    if path and path[0] == "limits":
        return []
    if path[:2] in NOT_LIMITS:
        return []
    if isinstance(node, bool):
        return []
    if isinstance(node, (int, float)):
        return [".".join(path)]
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found += _numbers_outside_limits(value, path + (str(key),))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            found += _numbers_outside_limits(value, path + (str(i),))
    return found


def _limits(raw: dict) -> dict[str, Limit]:
    limits: dict[str, Limit] = {}
    for name, entry in raw.get("limits", {}).items():
        if not isinstance(entry, dict):
            raise ConfigError(f"limit {name!r} must be a table with value, kind and source")
        value, kind, source = entry.get("value"), entry.get("kind"), entry.get("source")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigError(f"limit {name!r} has no numeric value")
        if kind not in LIMIT_KINDS:
            raise ConfigError(f"limit {name!r} has kind {kind!r}, expected one of {sorted(LIMIT_KINDS)}")
        if not isinstance(source, str) or not source.strip():
            raise ConfigError(f"limit {name!r} has no source. State its source or remove it.")
        limits[name] = Limit(name, float(value), kind, source.strip())
    return limits


def _wake_rules(raw: dict, limits: dict[str, Limit]) -> list[WakeRule]:
    rules: list[WakeRule] = []
    for i, entry in enumerate(raw.get("wake", [])):
        action = entry.get("action")
        if action not in WAKE_ACTIONS:
            raise ConfigError(f"wake rule {i} has action {action!r}")
        after = entry.get("after_limit")
        if action == "wake_after" and after not in limits:
            raise ConfigError(f"wake rule {i} names limit {after!r}, which is not in [limits]")
        rules.append(WakeRule(i, dict(entry.get("match", {})), action, after))
    return rules


def load(path: Path) -> Config:
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    stray = _numbers_outside_limits(raw, ())
    if stray:
        raise ConfigError(
            "numbers outside [limits]: " + ", ".join(stray)
            + ". Move each into [limits] with its source, or remove it."
        )
    limits = _limits(raw)
    root = path.parent
    paths = raw.get("paths", {})
    uids = list(raw.get("identity", {}).get("operator_uids", []))
    return Config(
        name=raw.get("steward", {}).get("name", "steward"),
        state_dir=root / paths.get("state", "state"),
        tracker_dir=root / paths.get("tracker", "tracker"),
        # An empty list means the account that runs the steward is the operator.
        operator_uids=uids or [os.getuid()],
        owners=dict(raw.get("owners", {})),
        labels=list(raw.get("labels", {}).get("allowed", [])),
        wake_rules=_wake_rules(raw, limits),
        limits=limits,
        github=dict(raw.get("github", {})),
    )
