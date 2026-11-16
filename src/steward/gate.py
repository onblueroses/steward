"""The wake gate: an ordered rule table decides which pending events start a turn.

The first rule that matches an event wins. A rule can wake at once, wake after a delay from
`[limits]`, or defer. A deferred event starts no turn, but the next turn still offers it.
The gate keeps no timers in memory: every due time comes from the event's own time on disk.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config, WakeRule
from .inbox import Event


@dataclass
class Plan:
    wake: list[tuple[Event, WakeRule]] = field(default_factory=list)
    waiting: list[tuple[Event, float]] = field(default_factory=list)  # (event, due time)
    deferred: list[Event] = field(default_factory=list)


DEFAULT_RULE = WakeRule(index=-1, match={}, action="defer")


class WakeGate:
    def __init__(self, config: Config):
        self.rules = config.wake_rules
        self.config = config

    def rule_for(self, event: Event) -> WakeRule:
        for rule in self.rules:
            if all(getattr(event, key, None) == value for key, value in rule.match.items()):
                return rule
        return DEFAULT_RULE

    def plan(self, pending: list[Event], now: float) -> Plan:
        plan = Plan()
        for event in pending:
            rule = self.rule_for(event)
            if rule.action == "wake":
                plan.wake.append((event, rule))
            elif rule.action == "wake_after":
                due = event.at + (self.config.limit(rule.after_limit or "") or 0.0)
                if due <= now:
                    plan.wake.append((event, rule))
                else:
                    plan.waiting.append((event, due))
            else:
                plan.deferred.append(event)
        return plan
