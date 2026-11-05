"""
The Workforce Manager agent: one object that forecasts, plans, routes, reacts and flags for approval.

    from Workforce_Manager import WorkforceManager, WFMConfig, run_baseline
    wfm = WorkforceManager(WFMConfig(roster_seniors=4, roster_juniors=8))
    sim = wfm.simulate("fin_de_mois", duration_min=120, seed=7)
    base = run_baseline("fin_de_mois", duration_min=120, seed=7, seniors=2, juniors=3)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Optional

from .approvals import ApprovalGate, callback_supervisor
from .forecast import Forecast, forecast_calls
from .reviews import ReviewStore
from .routing import make_router
from .simulation import WorkforceSimulation
from .staffing import ReactiveController, StaffingPlan, erlang_plan, fixed_plan
from .team import build_roster


@dataclass
class WFMConfig:
    roster_seniors: int = 4            # advisors available (planned + reserve)
    roster_juniors: int = 8
    senior_cost: float = 15.0          # per hour (synthetic)
    junior_cost: float = 10.0
    routing: str = "skills"            # skills | priority | fifo
    overflow_s: Optional[float] = 120  # security call may go to a junior after this wait (None = never)
    staffing: str = "erlang"           # erlang | fixed
    fixed_seniors: int = 2             # used when staffing == "fixed"
    fixed_juniors: int = 3
    reactive: bool = True
    control_s: float = 60
    approvals: bool = True
    sla_s: float = 60                  # answer within ... seconds
    sla_target: float = 0.8            # ... for this share of the callers
    max_occupancy: float = 0.85
    interval_min: float = 15
    history_days: int = 5
    aht_senior_s: float = 90           # baseline_advisor: ~3 exchanges of 30 s
    aht_junior_s: float = 60           # naive_advisor:    ~2 exchanges of 30 s
    callbacks: bool = True             # overload mode: offer a call-back instead of a long wait on hold
    offer_callback_after_s: float = 120


class WorkforceManager:
    def __init__(self, config: Optional[WFMConfig] = None, reviews: Optional[ReviewStore] = None, model=None):
        self.config = config or WFMConfig()
        self.reviews = reviews if reviews is not None else ReviewStore(":memory:")
        self.model = model             # LearnedModel: forecast + call lengths learned from the call log

    @classmethod
    def from_data(cls, model, seniors: int, juniors: int, sla_s: float = 60, sla_target: float = 0.8,
                  reviews: Optional[ReviewStore] = None) -> "WorkforceManager":
        """Every parameter comes from the call log or from the service goal (see model.py)."""
        cfg = WFMConfig(roster_seniors=seniors, roster_juniors=juniors, sla_s=sla_s, sla_target=sla_target,
                        aht_senior_s=model.aht_senior_s, aht_junior_s=model.aht_junior_s,
                        max_occupancy=1.0,                       # no extra cap: the service goal decides
                        overflow_s=sla_s,                        # a risky caller waits for a senior up to the goal
                        offer_callback_after_s=model.offer_after_s(sla_s))
        return cls(cfg, reviews, model)

    def forecast(self, scenario: str, duration_min: float, *, difficulty: float = 0.0, start_hour: float = 8.0,
                 realistic: bool = True) -> Forecast:
        c = self.config
        if self.model is not None:
            return self.model.forecast(scenario, start_hour, duration_min)
        return forecast_calls(scenario, duration_min, interval_min=c.interval_min, history_days=c.history_days,
                              difficulty=difficulty, start_hour=start_hour, realistic=realistic)

    def plan(self, forecast: Forecast, duration_min: Optional[float] = None) -> StaffingPlan:
        c = self.config
        if c.staffing == "fixed":
            duration_s = (duration_min * 60) if duration_min else forecast.intervals[-1].end_s
            return fixed_plan(duration_s, min(c.fixed_seniors, c.roster_seniors),
                              min(c.fixed_juniors, c.roster_juniors), forecast.interval_s)
        return erlang_plan(forecast, c.roster_seniors, c.roster_juniors, aht_senior_s=c.aht_senior_s,
                           aht_junior_s=c.aht_junior_s, sla_s=c.sla_s, sla_target=c.sla_target,
                           max_occupancy=c.max_occupancy)

    def simulate(self, scenario: str = "normal", duration_min: float = 120, seed: int = 0, *,
                 difficulty: float = 0.0, start_hour: float = 8.0, realistic: bool = True,
                 senior_fn: Optional[Callable] = None, junior_fn: Optional[Callable] = None,
                 llm: Optional[Callable] = None, judge_llm: Optional[Callable] = None,
                 auto_review: bool = False, label: str = "wfm") -> WorkforceSimulation:
        """Run one day with the Workforce Manager. auto_review=True lets the simulated supervisor
        (call-back procedure) decide the reviews; otherwise they stay pending for a real person."""
        c = self.config
        fc = self.forecast(scenario, duration_min, difficulty=difficulty, start_hour=start_hour, realistic=realistic)
        plan = self.plan(fc, duration_min)
        roster_kw = dict(senior_cost=c.senior_cost, junior_cost=c.junior_cost)
        if senior_fn:
            roster_kw["senior_fn"] = senior_fn
        if junior_fn:
            roster_kw["junior_fn"] = junior_fn
        sim = WorkforceSimulation(
            scenario, duration_min, seed, roster=build_roster(c.roster_seniors, c.roster_juniors, **roster_kw),
            plan=plan, router=make_router(c.routing, c.overflow_s),
            reactive=(ReactiveController(c.control_s, add_after_s=c.sla_s, aht_s=self.model.aht_all_s)
                      if self.model is not None else ReactiveController(c.control_s, add_after_s=c.sla_s / 2))
            if c.reactive else None,
            gate=ApprovalGate(self.reviews) if c.approvals else None, difficulty=difficulty,
            start_hour=start_hour, realistic=realistic, llm=llm, judge_llm=judge_llm, sla_s=c.sla_s, label=label,
            callbacks=c.callbacks,
            aht_s=self.model.aht_all_s if self.model is not None else (c.aht_senior_s + c.aht_junior_s) / 2,
            offer_after_s=c.offer_callback_after_s)
        sim.forecast = fc
        sim.config = asdict(c)
        sim.run()
        if sim.gate is not None and auto_review:
            callback_supervisor(self.reviews, sim.run_id)
        return sim


def run_baseline(scenario: str = "normal", duration_min: float = 120, seed: int = 0, *, seniors: int = 2,
                 juniors: int = 3, routing: str = "fifo", senior_cost: float = 15.0, junior_cost: float = 10.0,
                 sla_s: float = 60, interval_min: float = 15, difficulty: float = 0.0, start_hour: float = 8.0,
                 realistic: bool = True, llm: Optional[Callable] = None, judge_llm: Optional[Callable] = None,
                 senior_fn: Optional[Callable] = None, junior_fn: Optional[Callable] = None,
                 label: str = "today") -> WorkforceSimulation:
    """Today's call center: the same team all day, first come first served, no approval step."""
    roster_kw = dict(senior_cost=senior_cost, junior_cost=junior_cost)
    if senior_fn:
        roster_kw["senior_fn"] = senior_fn
    if junior_fn:
        roster_kw["junior_fn"] = junior_fn
    sim = WorkforceSimulation(
        scenario, duration_min, seed, roster=build_roster(seniors, juniors, **roster_kw),
        plan=fixed_plan(duration_min * 60, seniors, juniors, interval_min * 60), router=make_router(routing),
        reactive=None, gate=None, difficulty=difficulty, start_hour=start_hour, realistic=realistic,
        llm=llm, judge_llm=judge_llm, sla_s=sla_s, label=label)
    sim.forecast = None
    sim.config = dict(staffing="fixed", seniors=seniors, juniors=juniors, routing=routing, approvals=False,
                      reactive=False, senior_cost=senior_cost, junior_cost=junior_cost, sla_s=sla_s)
    sim.run()
    return sim
