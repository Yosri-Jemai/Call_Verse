"""
What the Workforce Manager learns from the call log (data/call_log.csv). No number is chosen by hand.

  forecast      calls per 15-minute slot, per day type = mean over the TRAINING days (+ spread),
                checked on the held-out TEST day (mean absolute error, error on the day's total)
  call length   mean conversation time of the answered calls: risky calls answered by a senior,
                simple calls answered by a junior (the two numbers Erlang-C needs)
  patience      how long customers accept to wait before hanging up: Kaplan-Meier survival curve.
                Customers who hung up give their patience; customers who were answered only tell us
                "at least that long" (censored), which Kaplan-Meier handles correctly.

Derived rules (only from these numbers and the service goal you set):
  * staffing         Erlang-C: smallest team reaching the goal (e.g. 80 % answered within 60 s)
  * call in reserve  when the wait announced to a new caller, or the longest wait, passes the goal
  * risky overflow   a risky caller waits for a senior only up to the goal, then any advisor (approval still on)
  * call-back offer  when the announced wait is longer than what half of the customers accept (learned)
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .dataset import CALL_LOG, SLOT_MIN, load_call_log
from .forecast import Forecast, IntervalForecast


def kaplan_meier(durations: list[float], events: list[int]) -> list[tuple[float, float]]:
    """[(t, S(t))]: share of customers still willing to wait after t seconds."""
    pairs = sorted(zip(durations, events))
    n = len(pairs)
    curve, s, i = [(0.0, 1.0)], 1.0, 0
    while i < len(pairs):
        t = pairs[i][0]
        d = c = 0
        while i < len(pairs) and pairs[i][0] == t:
            d += pairs[i][1]
            c += 1
            i += 1
        if d:
            s *= 1 - d / n
            curve.append((t, s))
        n -= c
    return curve


def km_quantile(curve: list[tuple[float, float]], share_hung_up: float) -> Optional[float]:
    """Wait after which `share_hung_up` of the customers have hung up (None if never reached in the data)."""
    for t, s in curve:
        if s <= 1 - share_hung_up:
            return t
    return None


@dataclass
class LearnedModel:
    calls: int
    train_days: dict                      # day_type -> number of training days
    test_days: dict
    slot_mean: dict                       # day_type -> {slot: mean calls}
    slot_sd: dict
    slot_security: dict                   # day_type -> {slot: mean risky calls}
    aht_senior_s: float                   # risky call answered by a senior
    aht_junior_s: float                   # simple call answered by a junior
    aht_all_s: float
    aht_n: dict
    patience_curve: list                  # Kaplan-Meier
    patience_median_s: Optional[float]
    patience_n: dict
    test_error: dict = field(default_factory=dict)
    source: str = ""

    # ---- forecast ------------------------------------------------------------
    def forecast(self, day_type: str, start_hour: float, duration_min: float) -> Forecast:
        if day_type not in self.slot_mean:
            raise KeyError(f"no logged day of type {day_type!r}")
        interval_s = SLOT_MIN * 60
        n = max(1, math.ceil(duration_min / SLOT_MIN))
        first = int(start_hour * 60 // SLOT_MIN)
        out = Forecast(day_type, interval_s, self.train_days[day_type])
        for i in range(n):
            slot = first + i
            out.intervals.append(IntervalForecast(i, i * interval_s, min((i + 1) * interval_s, duration_min * 60),
                                                  round(self.slot_mean[day_type].get(slot, 0.0), 2),
                                                  round(self.slot_security[day_type].get(slot, 0.0), 2)))
        return out

    def offer_after_s(self, sla_s: float) -> float:
        """Offer a call-back when the announced wait is longer than what half of the customers accept."""
        return self.patience_median_s if self.patience_median_s is not None else max(sla_s, self.patience_curve[-1][0])

    def summary(self) -> dict:
        curve = [(round(t), round(s, 3)) for t, s in self.patience_curve]
        step = max(1, len(curve) // 40)
        return dict(
            source=self.source, calls=self.calls, train_days=self.train_days, test_days=self.test_days,
            call_length=dict(risky_by_senior_s=round(self.aht_senior_s, 1), simple_by_junior_s=round(self.aht_junior_s, 1),
                             all_s=round(self.aht_all_s, 1), measured_on=self.aht_n),
            patience=dict(median_s=None if self.patience_median_s is None else round(self.patience_median_s),
                          quarter_hung_up_after_s=_r(km_quantile(self.patience_curve, 0.25)),
                          three_quarters_hung_up_after_s=_r(km_quantile(self.patience_curve, 0.75)),
                          measured_on=self.patience_n, curve=curve[::step] + ([curve[-1]] if curve[-1:] != curve[::step][-1:] else [])),
            forecast_test_error=self.test_error,
            profile={dt: {f"{s * SLOT_MIN // 60:02d}:{s * SLOT_MIN % 60:02d}": round(v, 1) for s, v in sorted(m.items())}
                     for dt, m in self.slot_mean.items()},
        )


def _r(x):
    return None if x is None else round(x)


def _per_slot(rows: list[dict]) -> dict:
    """day_id -> {slot: (calls, risky calls)}"""
    out: dict = defaultdict(lambda: defaultdict(lambda: [0, 0]))
    for r in rows:
        c = out[r["day_id"]][r["slot"]]
        c[0] += 1
        c[1] += r["category"] == "security"
    return out


def learn(path: Path = CALL_LOG, rows: Optional[list[dict]] = None) -> LearnedModel:
    rows = rows if rows is not None else load_call_log(path)
    train = [r for r in rows if r["split"] == "train"]
    test = [r for r in rows if r["split"] == "test"]
    days_of = lambda rs: {dt: len({r["day_id"] for r in rs if r["day_type"] == dt}) for dt in sorted({r["day_type"] for r in rs})}  # noqa: E731
    train_days, test_days = days_of(train), days_of(test)
    slots = sorted({r["slot"] for r in rows})

    # forecast: mean per slot over the training days of the same type
    per_day = _per_slot(train)
    day_type = {r["day_id"]: r["day_type"] for r in rows}
    slot_mean, slot_sd, slot_sec = {}, {}, {}
    for dt in train_days:
        ids = [d for d in per_day if day_type[d] == dt]
        slot_mean[dt], slot_sd[dt], slot_sec[dt] = {}, {}, {}
        for s in slots:
            vals = [per_day[d][s][0] if s in per_day[d] else 0 for d in ids]
            sec = [per_day[d][s][1] if s in per_day[d] else 0 for d in ids]
            slot_mean[dt][s] = statistics.mean(vals)
            slot_sd[dt][s] = statistics.stdev(vals) if len(vals) > 1 else 0.0
            slot_sec[dt][s] = statistics.mean(sec)

    # test: how far was the forecast from the held-out day?
    test_error = {}
    test_slots = _per_slot(test)
    for d, counts in test_slots.items():
        dt = day_type[d]
        actual = [counts[s][0] if s in counts else 0 for s in slots]
        pred = [slot_mean[dt][s] for s in slots]
        mae = statistics.mean(abs(a - p) for a, p in zip(actual, pred))
        total_a, total_p = sum(actual), sum(pred)
        test_error[dt] = dict(day=d, mae_calls_per_slot=round(mae, 1),
                              mean_calls_per_slot=round(statistics.mean(actual), 1),
                              total_actual=total_a, total_forecast=round(total_p),
                              total_error_pct=round(100 * (total_p - total_a) / max(total_a, 1), 1))

    # call length (answered calls only)
    answered = [r for r in train if not r["abandoned"] and r["talk_s"] > 0]
    sen = [r["talk_s"] for r in answered if r["category"] == "security" and r["advisor_skill"] == "senior"]
    jun = [r["talk_s"] for r in answered if r["category"] != "security" and r["advisor_skill"] == "junior"]
    all_ = [r["talk_s"] for r in answered]

    # patience: Kaplan-Meier on the waits (hung up = event, answered = censored)
    waited = [r for r in train if r["status"] != "callback_tomorrow"]
    curve = kaplan_meier([r["waited_s"] for r in waited], [r["abandoned"] for r in waited])
    return LearnedModel(
        calls=len(rows), train_days=train_days, test_days=test_days, slot_mean=slot_mean, slot_sd=slot_sd,
        slot_security=slot_sec, aht_senior_s=statistics.mean(sen or all_), aht_junior_s=statistics.mean(jun or all_),
        aht_all_s=statistics.mean(all_), aht_n=dict(risky_by_senior=len(sen), simple_by_junior=len(jun), all=len(all_)),
        patience_curve=curve, patience_median_s=km_quantile(curve, 0.5),
        patience_n=dict(customers=len(waited), hung_up=sum(r["abandoned"] for r in waited)),
        test_error=test_error, source=str(path))


_CACHE: dict = {}


def cached_model(path: Path = CALL_LOG) -> LearnedModel:
    """Learned once per version of the file."""
    p = Path(path)
    key = (str(p), p.stat().st_mtime if p.exists() else None)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = learn(p)
    return _CACHE[key]
