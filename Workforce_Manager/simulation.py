"""
The call-center day, run by the Workforce Manager (SimPy).

Same callers as Client_Simulator/sim_env.py for the same seed (same generator, same talker),
but instead of one fixed pool of identical advisors:
  * a roster of named seniors / juniors, each going on and off duty (staffing plan + reactive control),
  * a router that picks which caller each free advisor takes,
  * every advisor goes through the human-approval gate (optional),
  * overload mode (optional): when the line is too long, simple callers are offered a call-back
    instead of waiting on hold; the call-backs are made as soon as nobody is waiting.
`sim_env.py` is not modified; its KPI function is reused by the reports.
"""
from __future__ import annotations

import uuid
from typing import Callable, Optional

import simpy

from client_simulator import ClientSimulatorAgent, advisor_view
from sim_env import TURN_SECONDS

from .approvals import ApprovalGate
from .routing import Router, Waiting, call_category
from .staffing import ReactiveController, StaffingPlan
from .team import SENIOR, SKILLS, Advisor


class WorkforceSimulation:
    def __init__(self, scenario: str = "normal", duration_min: float = 60, seed: int = 0, *,
                 roster: list[Advisor], plan: StaffingPlan, router: Router,
                 reactive: Optional[ReactiveController] = None, gate: Optional[ApprovalGate] = None,
                 difficulty: float = 0.0, start_hour: float = 8.0, realistic: bool = True,
                 llm: Optional[Callable[[str], str]] = None, judge_llm: Optional[Callable[[str], str]] = None,
                 sla_s: float = 60, label: str = "wfm", callbacks: bool = False, aht_s: float = 75,
                 offer_after_s: float = 120, alert_every_s: float = 600):
        self.label = label
        self.run_id = f"{label}-{uuid.uuid4().hex[:8]}"
        self.env = simpy.Environment()
        self.scenario, self.seed, self.difficulty, self.start_hour = scenario, seed, difficulty, start_hour
        self.duration = duration_min * 60
        self.sla_s = sla_s
        self.roster, self.plan, self.router, self.reactive, self.gate = roster, plan, router, reactive, gate
        if gate is not None:
            gate.run_id = self.run_id
        # identical to CallCenterEnv: one agent makes the arrivals, one talks
        self.generator = ClientSimulatorAgent(seed=seed, difficulty=difficulty)
        self.requests = self.generator.generate_requests(scenario, duration_min, difficulty=difficulty,
                                                         start_hour=start_hour, realistic=realistic)
        self.incidents = self.generator.incidents
        self.talker = ClientSimulatorAgent(seed=seed + 1, llm=llm, judge_llm=judge_llm, difficulty=difficulty)
        self.talker.crm = self.generator.crm
        self.queue: list[Waiting] = []
        self.extra = {s: 0 for s in SKILLS}          # reserve advisors called in by the reactive controller
        self.results: list[dict] = []
        self.transcripts: dict[str, list] = {}
        self.decisions: list[dict] = []              # reactive controller log
        self.end_time: float = 0.0
        # event log, replayed by the dashboard's control room (t = seconds since opening)
        self.events: list[dict] = []
        self._why = "opening"
        # overload mode
        self.callbacks, self.aht_s, self.offer_after_s = callbacks, aht_s, offer_after_s
        self.alert_every_s, self._last_alert = alert_every_s, -1e9
        self.callback_list: list[Waiting] = []       # customers who accepted to be called back
        self.tomorrow = 0
        self.closed = False

    def _ev(self, kind: str, **data) -> None:
        self.events.append(dict(t=round(self.env.now, 1), kind=kind, **data))

    # ---- staffing ------------------------------------------------------------
    def _members(self, skill: str) -> list[Advisor]:
        return [a for a in self.roster if a.skill == skill]

    def target(self, skill: str) -> int:
        planned = self.plan.at(min(self.env.now, self.duration - 1e-6)).count(skill)
        return max(0, min(len(self._members(skill)), planned + self.extra[skill]))

    def can_add(self, skill: str) -> bool:
        planned = self.plan.at(min(self.env.now, self.duration - 1e-6)).count(skill)
        return planned + self.extra[skill] < len(self._members(skill))

    def apply_targets(self) -> None:
        now = self.env.now
        for skill in SKILLS:
            members = self._members(skill)
            active = [a for a in members if a.on_duty and not a.leaving]
            target = self.target(skill)
            if len(active) < target:
                need = target - len(active)
                for a in members:                       # first keep the ones about to leave
                    if need and a.on_duty and a.leaving:
                        a.leaving, need = False, need - 1
                        self._ev("stay", advisor=a.name, skill=a.skill, why=self._why)
                for a in members:
                    if need and not a.on_duty:
                        a.start_shift(now)
                        need -= 1
                        self._ev("on", advisor=a.name, skill=a.skill, why=self._why)
            elif len(active) > target:
                surplus = len(active) - target
                for a in sorted(active, key=lambda x: (x.busy, -x.shift_start)):   # idle and latest first
                    if not surplus:
                        break
                    if a.busy:
                        a.leaving = True
                        self._ev("leaving", advisor=a.name, skill=a.skill, why=self._why)
                    else:
                        a.end_shift(now)
                        self._ev("off", advisor=a.name, skill=a.skill, why=self._why)
                    surplus -= 1
        self._dispatch()

    def _staffing(self):
        for iv in self.plan.intervals[1:]:
            yield self.env.timeout(max(0.0, iv.start_s - self.env.now))
            self._why = "plan"
            self._ev("plan", seniors=iv.seniors, juniors=iv.juniors, forecast=iv.forecast_calls)
            self.apply_targets()

    def _controller(self):
        while self.env.now < self.duration:
            yield self.env.timeout(self.reactive.control_s)
            if self.env.now >= self.duration:
                break
            note = self.reactive.step(self)
            if not note:
                self._maybe_alert()
            if note:
                self._why = "reactive_add" if note.startswith("+") else "reactive_release"
                self._ev("decision", text=note, add=note.startswith("+"), waiting=len(self.queue),
                         longest=round(max((e.waited(self.env.now) for e in self.queue), default=0.0)))
                self.apply_targets()
                self.decisions.append(dict(t=round(self.env.now), decision=note,
                                           on_duty={s: sum(a.on_duty and not a.leaving for a in self._members(s))
                                                    for s in SKILLS}))

    def _maybe_alert(self) -> None:
        """The line is too long and every advisor of the roster is already working: say so."""
        now = self.env.now
        longest = max((e.waited(now) for e in self.queue), default=0.0)
        if not self.queue or longest < self.reactive.add_after_s or any(self.can_add(s) for s in SKILLS):
            return
        if now - self._last_alert < self.alert_every_s:
            return
        self._last_alert = now
        self._ev("alert", waiting=len(self.queue), longest=round(longest), working=self.working(),
                 roster=len(self.roster), callbacks=len(self.callback_list))

    def working(self) -> int:
        return sum(a.on_duty and not a.leaving for a in self.roster)

    def estimated_wait(self) -> float:
        """What the Workforce Manager tells a new caller: people ahead x average call / advisors working."""
        if any(a.on_duty and not a.busy and not a.leaving for a in self.roster):
            return 0.0
        return len(self.queue) * self.aht_s / max(self.working(), 1)

    # ---- routing -------------------------------------------------------------
    def _dispatch(self) -> None:
        while True:
            free = [a for a in self.roster if a.on_duty and not a.busy and not a.leaving]
            if not free:
                return
            pick = self.router.choose(free, self.queue, self.env.now) if self.queue else None
            if pick is not None:
                adv, entry, reason = pick
                self.queue.remove(entry)
                adv.busy, adv.current = True, entry.req.request_id
                entry.advisor, entry.reason = adv, reason
                self._ev("assign", call=entry.req.request_id, advisor=adv.name, skill=adv.skill, reason=reason,
                         waited=round(entry.waited(self.env.now)))
                entry.event.succeed()
                continue
            if not self.callback_list or self.closed:    # call-backs only when no caller on hold can be served
                return
            cb = self.callback_list.pop(0)
            juniors = [a for a in free if a.skill != SENIOR]
            adv = min(juniors or free, key=lambda a: a.idle_since)
            adv.busy, adv.current = True, cb.req.request_id
            cb.advisor, cb.reason = adv, "call-back"
            self._ev("callback", call=cb.req.request_id, advisor=adv.name, skill=adv.skill,
                     after=round(cb.waited(self.env.now)))
            cb.event.succeed()

    def _wake(self, delay: float):
        yield self.env.timeout(delay)
        self._dispatch()

    def _release(self, adv: Advisor, talk_s: float, count: bool = True) -> None:
        adv.busy, adv.current, adv.idle_since = False, None, self.env.now
        adv.busy_s += talk_s
        adv.calls += int(count)
        if adv.leaving:
            adv.end_shift(self.env.now)
            self._ev("off", advisor=adv.name, skill=adv.skill, why="finished_call")
        self._dispatch()

    # ---- one caller ----------------------------------------------------------
    def _client(self, req):
        yield self.env.timeout(req.arrival_time)
        entry = Waiting(req, self.env.now, self.env.event(), call_category(req.intent))
        self.queue.append(entry)
        self._ev("arrive", call=req.request_id, name=req.client_name, intent=req.intent, category=entry.category,
                 urgency=req.urgency)
        # overload mode: a simple, non-urgent caller facing a long wait is offered a call-back
        if self.callbacks and entry.category != "security" and req.urgency < 3:
            est = self.estimated_wait()
            if est >= self.offer_after_s:
                accepted = est > req.patience            # he would not have waited that long anyway
                self._ev("offer", call=req.request_id, estimated=round(est), accepted=accepted)
                if accepted:
                    self.queue.remove(entry)
                    entry.reason = "call-back"
                    self.callback_list.append(entry)
                    self._dispatch()
                    yield entry.event
                    yield from self._talk(req, entry, entry.advisor, 0.0, callback_after=entry.waited(self.env.now))
                    return
        wake = self.router.wake_after(entry)
        if wake is not None:
            self.env.process(self._wake(wake))
        self._dispatch()
        if not entry.event.triggered:
            yield entry.event | self.env.timeout(req.patience)
        wait = self.env.now - entry.arrived
        if not entry.event.triggered:                    # patience exhausted in the queue
            if entry in self.queue:
                self.queue.remove(entry)
            st = self.talker.start(req, wait_seconds=wait)
            st.status = "abandoned"
            self._ev("abandon", call=req.request_id, waited=round(wait))
            self._finish(st, entry, None, 0.0, {})
            return
        yield from self._talk(req, entry, entry.advisor, wait)

    def _talk(self, req, entry: Waiting, adv: Advisor, wait: float, callback_after: Optional[float] = None):
        st = self.talker.start(req, wait_seconds=wait)
        if st.status == "abandoned":                     # answered at the very moment he gave up
            self._ev("abandon", call=req.request_id, waited=round(wait), advisor=adv.name)
            self._release(adv, 0.0, count=False)
            entry.advisor = None
            self._finish(st, entry, None, 0.0, {})
            return
        ctx: dict = {}
        fn = self.gate.wrap(adv.fn, req, adv, ctx) if self.gate is not None else adv.fn
        crm = self.generator.crm.get
        talk_start = self.env.now
        while st.status == "active":
            msg = fn(advisor_view(st, crm))
            yield self.env.timeout(TURN_SECONDS)
            self.talker.reply(st, msg)
        talk = self.env.now - talk_start
        self._ev("end", call=req.request_id, advisor=adv.name, status=st.status, held=bool(ctx.get("held")),
                 review_id=ctx.get("review_id"), category=ctx.get("category"), turns=st.turn)
        self._release(adv, talk)
        self._finish(st, entry, adv, talk, ctx, callback_after)

    def _finish(self, st, entry: Waiting, adv: Optional[Advisor], talk_s: float, ctx: dict,
                callback_after: Optional[float] = None) -> None:
        s = self.talker.summary(st)
        s.update(callback=callback_after is not None or st.status == "callback_tomorrow",
                 callback_after_s=None if callback_after is None else round(callback_after, 1))
        s.update(arrival_s=round(entry.arrived, 1), category=entry.category,
                 advisor=adv.name if adv else None, advisor_skill=adv.skill if adv else None,
                 routing=entry.reason, talk_s=round(talk_s, 1), review_id=ctx.get("review_id"),
                 review_category=ctx.get("category"), held_for_review=bool(ctx.get("held")))
        self.results.append(s)
        self.transcripts[st.request.request_id] = list(st.history)

    # ---- run -----------------------------------------------------------------
    def run(self) -> list[dict]:
        first = self.plan.intervals[0]
        self._ev("plan", seniors=first.seniors, juniors=first.juniors, forecast=first.forecast_calls)
        self._why = "opening"
        self.apply_targets()                             # staff of the first interval at t = 0
        for req in self.requests:
            self.env.process(self._client(req))
        self.env.process(self._staffing())
        if self.reactive is not None:
            self.env.process(self._controller())
        self.env.run(until=self.duration + 1800)        # let in-flight and queued calls finish
        self.closed = True                               # no new call-back after this point
        while any(a.busy for a in self.roster) and self.env.peek() < float("inf"):
            self.env.step()                              # finish the conversations still in progress
        self.end_time = self.env.now
        for cb in self.callback_list:                    # not called back before closing: first thing tomorrow
            st = self.talker.start(cb.req, wait_seconds=0)
            st.status = "callback_tomorrow"
            self._finish(st, cb, None, 0.0, {})
        self.tomorrow = len(self.callback_list)
        for a in self.roster:
            if a.on_duty:
                a.end_shift(self.end_time)
        return self.results

    # ---- helpers for reports ---------------------------------------------------
    def staffing_cost(self) -> float:
        """Cost of the advisors' on-duty time during opening hours (same horizon as sim_env.kpis)."""
        return sum(a.on_duty_seconds(0, self.duration) * a.cost_per_hour / 3600 for a in self.roster)

    def on_duty_average(self, start: float, end: float, skill: Optional[str] = None) -> float:
        members = [a for a in self.roster if skill is None or a.skill == skill]
        return sum(a.on_duty_seconds(start, end) for a in members) / max(end - start, 1e-9)

    def seniors_on_duty_seconds(self) -> float:
        return sum(a.on_duty_seconds(0, self.duration) for a in self._members(SENIOR))
