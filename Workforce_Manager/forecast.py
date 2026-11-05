"""
Forecast: how many calls (and how many security calls) to expect in each interval.

Like a real Workforce Manager, it learns from HISTORY, not from the day it is planning:
it generates `history_days` past days of the same scenario with other seeds (same hourly
profile, same kind of random incidents) and averages them per interval. The day being
simulated is never looked at, so surprises (an outage today) are not known in advance:
the reactive controller handles them.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from client_simulator import ClientSimulatorAgent

from .routing import call_category


@dataclass
class IntervalForecast:
    index: int
    start_s: float
    end_s: float
    calls: float               # expected arrivals in the interval
    security_calls: float      # of which security calls (need a senior)

    @property
    def length_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def rate(self) -> float:
        """Calls per second."""
        return self.calls / max(self.length_s, 1.0)

    @property
    def security_rate(self) -> float:
        return self.security_calls / max(self.length_s, 1.0)


@dataclass
class Forecast:
    scenario: str
    interval_s: float
    history_days: int
    intervals: list = field(default_factory=list)

    @property
    def total_calls(self) -> float:
        return sum(i.calls for i in self.intervals)

    @property
    def security_share(self) -> float:
        return sum(i.security_calls for i in self.intervals) / max(self.total_calls, 1e-9)

    def rows(self) -> list[dict]:
        return [dict(asdict(i), rate_per_min=round(i.rate * 60, 2)) for i in self.intervals]


def forecast_calls(scenario: str, duration_min: float, *, interval_min: float = 15, history_days: int = 5,
                   history_seed: int = 10_000, difficulty: float = 0.0, start_hour: float = 8.0,
                   realistic: bool = True) -> Forecast:
    interval_s = interval_min * 60
    duration_s = duration_min * 60
    n = max(1, math.ceil(duration_s / interval_s))
    calls = [0.0] * n
    security = [0.0] * n
    days = max(1, history_days)
    for d in range(days):
        gen = ClientSimulatorAgent(seed=history_seed + d, difficulty=difficulty)
        for r in gen.generate_requests(scenario, duration_min, difficulty=difficulty,
                                       start_hour=start_hour, realistic=realistic):
            i = min(int(r.arrival_time // interval_s), n - 1)
            calls[i] += 1
            security[i] += call_category(r.intent) == "security"
    out = Forecast(scenario, interval_s, days)
    for i in range(n):
        start, end = i * interval_s, min((i + 1) * interval_s, duration_s)
        out.intervals.append(IntervalForecast(i, start, end, round(calls[i] / days, 2),
                                              round(security[i] / days, 2)))
    return out
