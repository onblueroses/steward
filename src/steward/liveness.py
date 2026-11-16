"""A liveness check that runs outside the agent. It reads files only and calls no model.

Exit 0: the last pass ended without failure. Exit 1: the last pass failed, or the oldest
pending event is older than the optional limit `liveness_pending_age_seconds`.
Exit 3: the check could not read the state. The repository sets no age limit by default,
because no measurement stands behind one. Measure your feed, then add it with its source.
"""

from __future__ import annotations

import json

from .config import Config


def check(config: Config, now: float) -> tuple[int, list[str]]:
    stamp_path = config.state_dir / "stamp.json"
    try:
        stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return 3, [f"no stamp at {stamp_path}: the steward has not run"]
    except (OSError, json.JSONDecodeError) as e:
        return 3, [f"cannot read {stamp_path}: {e}"]
    lines = [f"last pass {now - stamp['at']:.0f} s ago, ok={stamp['ok']}, pending={stamp['pending']}"]
    code = 0
    if not stamp["ok"]:
        code = 1
        lines += [f"failure: {f}" for f in stamp["failures"]]
    oldest = stamp.get("oldest_pending_at")
    limit = config.limit("liveness_pending_age_seconds")
    if oldest is not None:
        age = now - oldest
        lines.append(f"oldest pending event: {age:.0f} s")
        if limit is not None and age > limit:
            code = 1
            lines.append(f"stale: older than {limit:g} s ({config.limits['liveness_pending_age_seconds'].source})")
    return code, lines

