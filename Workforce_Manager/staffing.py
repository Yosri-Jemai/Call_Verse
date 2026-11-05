"""
Staffing: how many seniors and juniors are on duty in each interval.

  erlang_plan   from the forecast, with Erlang-C (planned before the day starts)
  fixed_plan    the same team all day (today's behaviour, used as the baseline)
  ReactiveController  intraday corrections: calls in a reserve advisor when the queue grows
                (incident, spike), releases him when the queue is empty again.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from .erlang import required_advisors, service_level
from .forecast import Forecast
from .team import JUNIOR, SENIOR


@dataclass
class IntervalPlan:
    index: int
    start_s: float
    end_s: float
    seniors: int
    juniors: int
    forecast_calls: float = 0.0
    forecast_security: float = 0.0
    needed_seniors: int = 0
    needed_total: int = 0
    expected_sl: Optional[float] = None
    shortage: bool = False             # the roster is too small for the target service level

    @property
    def total(self) -> int:
        return self.seniors + self.juniors

    def count(self, skill: str) -> int:
        return self.seniors if skill == SENIOR else self.juniors


@dataclass
class StaffingPlan:
    kind: str                          # "erlang" | "fixed"
    interval_s: float
    intervals: list = field(default_factory=list)

    def at(self, t: float) -> IntervalPlan:
        i = min(max(int(t // self.interval_s), 0), len(self.intervals) - 1)
        return self.intervals[i]

    def rows(self) -> list[dict]:
        return [dict(asdict(i), total=i.total) for i in self.intervals]


def erlang_plan(forecast: Forecast, roster_seniors: int, roster_juniors: int, *, aht_senior_s: float = 90,
                aht_junior_s: float = 60, sla_s: float = 60, sla_target: float = 0.8,
                max_occupancy: float = 0.85, min_seniors: int = 1) -> StaffingPlan:
    """Seniors are sized for the security calls; the rest of the load is given to juniors.
    If juniors run out, seniors fill in (they can take any call); juniors cannot replace seniors."""
    plan = StaffingPlan("erlang", forecast.interval_s)
    for f in forecast.intervals:
        share = f.security_calls / f.calls if f.calls else 0.0
        aht_pool = share * aht_senior_s + (1 - share) * aht_junior_s
        needed_total = required_advisors(f.rate, aht_pool, sla_s, sla_target, max_occupancy, minimum=1)
        needed_seniors = max(min_seniors, required_advisors(f.security_rate, aht_senior_s, sla_s, sla_target,
                                                            max_occupancy, minimum=min_seniors))
        needed_juniors = max(0, needed_total - needed_seniors)
        juniors = min(roster_juniors, needed_juniors)
        seniors = min(roster_seniors, needed_seniors + (needed_juniors - juniors))
        a = f.rate * aht_pool
        sl = service_level(seniors + juniors, a, aht_pool, sla_s) if seniors + juniors else 0.0
        plan.intervals.append(IntervalPlan(
            f.index, f.start_s, f.end_s, seniors, juniors, f.calls, f.security_calls, needed_seniors,
            max(needed_total, needed_seniors), round(sl, 3),
            shortage=seniors < needed_seniors or seniors + juniors < needed_total))
    return plan


def team_check(plan: StaffingPlan, roster_seniors: int, roster_juniors: int, *, aht_senior_s: float = 90,
               aht_junior_s: float = 60, max_occupancy: float = 0.85) -> dict:
    """Before the day: is the team big enough for the forecast? If not, by how much, and what is missing."""
    rows, missing_calls = [], 0.0
    for iv in plan.intervals:
        share = iv.forecast_security / iv.forecast_calls if iv.forecast_calls else 0.0
        aht = share * aht_senior_s + (1 - share) * aht_junior_s
        length = iv.end_s - iv.start_s
        capacity = (roster_seniors + roster_juniors) * length * max_occupancy / max(aht, 1.0)
        over = max(0.0, iv.forecast_calls - capacity)
        missing_calls += over
        rows.append(dict(start_s=iv.start_s, forecast=round(iv.forecast_calls, 1), capacity=round(capacity, 1),
                         need_seniors=iv.needed_seniors, need_total=iv.needed_total, shortage=iv.shortage))
    peak = max(rows, key=lambda r: r["need_total"]) if rows else None
    need_s = max((r["need_seniors"] for r in rows), default=1)
    need_t = max((r["need_total"] for r in rows), default=1)
    return dict(ok=not any(r["shortage"] for r in rows), have_seniors=roster_seniors, have_juniors=roster_juniors,
                need_seniors=need_s, need_juniors=max(0, need_t - need_s), need_total=need_t,
                peak_start_s=peak["start_s"] if peak else 0, short_intervals=sum(r["shortage"] for r in rows),
                intervals=len(rows), calls_over_capacity=round(missing_calls),
                forecast_calls=round(sum(r["forecast"] for r in rows)), rows=rows)


def fixed_plan(duration_s: float, seniors: int, juniors: int, interval_s: float = 900) -> StaffingPlan:
    plan = StaffingPlan("fixed", interval_s)
    i, t = 0, 0.0
    while t < duration_s or i == 0:
        plan.intervals.append(IntervalPlan(i, t, min(t + interval_s, duration_s), seniors, juniors))
        i, t = i + 1, t + interval_s
    return plan


class ReactiveController:
    """Every `control_s` seconds, looks at the live queue and corrects the plan.

    add one advisor   when someone has waited >= add_after_s, or >= queue_trigger callers wait
                      (a senior if security callers wait and no senior is free, else a junior)
    release one       extra advisor when nobody is waiting and that advisor is idle
    The extra advisors come from the roster's reserve (advisors not planned for this interval).
    """

    def __init__(self, control_s: float = 60, add_after_s: float = 30, queue_trigger: int = 3,
                 aht_s: Optional[float] = None):
        self.control_s = control_s
        self.add_after_s = add_after_s
        self.queue_trigger = queue_trigger
        self.aht_s = aht_s     # data-driven mode: trigger on the wait a new caller would get, not on a head count

    def overloaded(self, sim, longest: float) -> bool:
        queue = sim.queue
        if not queue:
            return False
        if longest >= self.add_after_s:
            return True
        if self.aht_s is not None:
            working = sum(a.on_duty and not a.leaving for a in sim.roster)
            return len(queue) * self.aht_s / max(working, 1) >= self.add_after_s
        return len(queue) >= self.queue_trigger

    def step(self, sim) -> Optional[str]:
        now = sim.env.now
        queue = sim.queue
        longest = max((e.waited(now) for e in queue), default=0.0)
        if self.overloaded(sim, longest):
            security_waiting = any(e.category == "security" for e in queue)
            senior_free = any(a.on_duty and not a.busy and not a.leaving and a.skill == SENIOR for a in sim.roster)
            first = SENIOR if security_waiting and not senior_free else JUNIOR
            for skill in (first, JUNIOR if first == SENIOR else SENIOR):
                if sim.can_add(skill):
                    sim.extra[skill] += 1
                    return (f"+1 {skill}: {len(queue)} waiting, longest {longest:.0f}s"
                            + (" (security calls waiting)" if security_waiting else ""))
            return None
        if not queue:
            for skill in (JUNIOR, SENIOR):
                idle = any(a.on_duty and not a.busy and a.skill == skill for a in sim.roster)
                if sim.extra[skill] > 0 and idle:
                    sim.extra[skill] -= 1
                    return f"-1 {skill}: queue empty, reserve advisor released"
        return None
