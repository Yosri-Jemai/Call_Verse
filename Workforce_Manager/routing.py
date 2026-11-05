"""
Routing: when an advisor is free, which waiting caller does he take?

The router only uses what a real call center knows before the call is answered: the reason of the
call chosen in the phone menu (intent -> category), its urgency, and how long the caller has waited.
It never uses hidden simulator data (is_fraudster, patience, mood).

  fifo      first come, first served, any advisor (today's behaviour)
  priority  most urgent first, any advisor (the existing "priority" baseline)
  skills    the Workforce Manager: security calls go to careful seniors, the rest to fast juniors,
            waiting time raises priority, and an overdue security call may overflow to a junior
            (still protected by the human-approval gate).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from client_simulator import FRAUD_INTENTS, INTENTS, LOW_RISK_INTENTS

from .team import JUNIOR, SENIOR, Advisor

# Calls that touch security (identity, money movement, fraud): they need a careful advisor.
SECURITY_INTENTS = frozenset(FRAUD_INTENTS) | frozenset(k for k, v in INTENTS.items() if v.get("trap")) | {
    "fraude_signalee", "prelevement_conteste"}
SIMPLE_INTENTS = frozenset(LOW_RISK_INTENTS)


def call_category(intent: str) -> str:
    if intent in SECURITY_INTENTS:
        return "security"
    if intent in SIMPLE_INTENTS:
        return "simple"
    return "standard"


@dataclass
class Waiting:
    """A caller in the queue."""
    req: Any                      # ClientRequest
    arrived: float
    event: Any                    # simpy event, triggered when an advisor is assigned
    category: str
    advisor: Optional[Advisor] = None
    reason: str = ""

    def waited(self, now: float) -> float:
        return now - self.arrived


def _longest_idle(advisors: list[Advisor]) -> Advisor:
    return min(advisors, key=lambda a: a.idle_since)


class Router:
    name = "base"

    def choose(self, free: list[Advisor], queue: list[Waiting], now: float) -> Optional[tuple]:
        """Return (advisor, waiting caller, reason) or None when no assignment should be made now."""
        raise NotImplementedError

    def wake_after(self, entry: Waiting) -> Optional[float]:
        """Seconds after which this caller's routing options change (the queue is re-examined)."""
        return None


class FifoRouter(Router):
    name = "fifo"

    def choose(self, free, queue, now):
        if not free or not queue:
            return None
        entry = min(queue, key=lambda e: e.arrived)
        return _longest_idle(free), entry, "fifo"


class PriorityRouter(Router):
    name = "priority"

    def choose(self, free, queue, now):
        if not free or not queue:
            return None
        entry = min(queue, key=lambda e: (-e.req.urgency, e.arrived))
        return _longest_idle(free), entry, "priority"


class SkillsRouter(Router):
    """Skill-based routing with waiting-time aging.

    score = urgency (1..3) + minutes waited * age_weight, so a low-urgency caller is not starved.
    overflow_s: after this wait a security call may go to a junior (None = never).
    """
    name = "skills"

    def __init__(self, overflow_s: Optional[float] = 120.0, age_weight: float = 1.0):
        self.overflow_s = overflow_s
        self.age_weight = age_weight

    def score(self, e: Waiting, now: float) -> float:
        return e.req.urgency + self.age_weight * e.waited(now) / 60.0

    def choose(self, free, queue, now):
        if not free or not queue:
            return None
        seniors = [a for a in free if a.skill == SENIOR]
        juniors = [a for a in free if a.skill == JUNIOR]
        security = [e for e in queue if e.category == "security"]
        other = [e for e in queue if e.category != "security"]
        best = lambda group: max(group, key=lambda e: (self.score(e, now), -e.arrived))  # noqa: E731

        if security and seniors:
            return _longest_idle(seniors), best(security), "security call -> senior"
        if other and juniors:
            e = best(other)
            return _longest_idle(juniors), e, f"{e.category} call -> junior"
        if security and juniors and self.overflow_s is not None:
            overdue = [e for e in security if e.waited(now) >= self.overflow_s]
            if overdue:
                return _longest_idle(juniors), best(overdue), "overflow: security call waited too long -> junior"
        if other and seniors:
            e = best(other)
            return _longest_idle(seniors), e, f"{e.category} call -> senior (no junior free)"
        return None

    def wake_after(self, entry):
        if entry.category == "security" and self.overflow_s is not None:
            return self.overflow_s
        return None


def make_router(name: str, overflow_s: Optional[float] = 120.0) -> Router:
    if name == "fifo":
        return FifoRouter()
    if name == "priority":
        return PriorityRouter()
    if name == "skills":
        return SkillsRouter(overflow_s=overflow_s)
    raise ValueError(f"unknown routing policy: {name!r} (fifo | priority | skills)")
