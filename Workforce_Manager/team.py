"""
The advisor team (roster) managed by the Workforce Manager.

Two skills, both built from the advisors that already exist in the Client Simulator:
  senior  careful: verifies identity against the CRM before acting (baseline_advisor). Longer calls.
  junior  fast: answers quickly but skips the identity check (naive_advisor). Shorter, cheaper calls.

Any `AdvisorView -> str` function can be used instead (llm_advisor, the real Customer Advisor...).
Each advisor keeps its shifts (on-duty periods) so the reports can compute cost and occupancy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from advisors import baseline_advisor, naive_advisor

SENIOR = "senior"
JUNIOR = "junior"
SKILLS = (SENIOR, JUNIOR)
SKILL_LABELS = {SENIOR: "senior (careful, verifies identity)", JUNIOR: "junior (fast, skips verification)"}


@dataclass
class Advisor:
    name: str
    skill: str
    fn: Callable
    cost_per_hour: float
    on_duty: bool = False
    busy: bool = False
    leaving: bool = False             # finishes the current call, then goes off duty
    idle_since: float = 0.0
    shifts: list = field(default_factory=list)      # [[start_s, end_s or None]]
    calls: int = 0
    busy_s: float = 0.0
    current: Optional[str] = None     # request_id of the call in progress

    def start_shift(self, now: float) -> None:
        self.on_duty, self.leaving, self.idle_since = True, False, now
        self.shifts.append([now, None])

    def end_shift(self, now: float) -> None:
        self.on_duty, self.leaving = False, False
        if self.shifts and self.shifts[-1][1] is None:
            self.shifts[-1][1] = now

    @property
    def shift_start(self) -> float:
        return self.shifts[-1][0] if self.shifts else 0.0

    def on_duty_seconds(self, start: float, end: float, now: Optional[float] = None) -> float:
        """Time on duty inside [start, end] (open shifts are counted until `now`, default `end`)."""
        total = 0.0
        for s, e in self.shifts:
            e = (now if now is not None else end) if e is None else e
            total += max(0.0, min(e, end) - max(s, start))
        return total


MANAGER_NAME = "WFM"   # the Workforce Manager itself, answering calls when nobody else is on the team


def effective_team(seniors: int, juniors: int) -> tuple[int, int]:
    """With an empty team the Workforce Manager takes the calls itself, as one careful (senior) desk."""
    return (1, 0) if seniors + juniors == 0 else (seniors, juniors)


def build_roster(seniors: int, juniors: int, *, senior_fn: Callable = baseline_advisor,
                 junior_fn: Callable = naive_advisor, senior_cost: float = 15.0,
                 junior_cost: float = 10.0) -> list[Advisor]:
    """Named advisors S1..Sn (senior) and J1..Jm (junior). Costs are per hour (synthetic dinars).
    0 + 0: the roster is the Workforce Manager alone (desk "WFM", careful like a senior)."""
    if seniors + juniors == 0:
        return [Advisor(MANAGER_NAME, SENIOR, senior_fn, senior_cost)]
    team = [Advisor(f"S{i + 1}", SENIOR, senior_fn, senior_cost) for i in range(seniors)]
    team += [Advisor(f"J{i + 1}", JUNIOR, junior_fn, junior_cost) for i in range(juniors)]
    return team
