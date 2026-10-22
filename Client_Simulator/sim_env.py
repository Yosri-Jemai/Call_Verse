"""
SimPy environment: the Digital Twin's clock, queue and advisors.

Clients (from the Client Simulator) arrive over time, wait for a free advisor,
abandon if their patience runs out, then converse until the call ends.
The assignment policy is the lever the Workforce Manager (RL) will control;
here two baselines are provided ("fifo" and "priority") so you can compare.

The advisor function receives an AdvisorView (history + caller id + a CRM lookup), never the
hidden ground truth. Pass a Store (db.py) to persist the run, its calls and its KPIs.
"""
from __future__ import annotations

import statistics
from typing import Callable, Optional

import simpy

from client_simulator import LOW_RISK_INTENTS, AdvisorView, ClientSimulatorAgent, advisor_view
from load_model import Curriculum
from advisors import baseline_advisor, naive_advisor      # noqa: F401  (re-exported)

TURN_SECONDS = 30   # time for one advisor <-> client exchange


class CallCenterEnv:
    def __init__(self, n_advisors: int = 5, scenario: str = "normal",
                 duration_min: float = 30, seed: int = 0,
                 policy: str = "fifo",
                 advisor_fn: Callable[[AdvisorView], str] = baseline_advisor,
                 llm: Optional[Callable[[str], str]] = None,
                 judge_llm: Optional[Callable[[str], str]] = None,
                 difficulty: float = 0.0, start_hour: float = 8.0, realistic: bool = True,
                 store=None, cost_per_advisor_hour: float = 12.0):
        self.env = simpy.Environment()
        self.advisors = simpy.PriorityResource(self.env, capacity=n_advisors)
        self.n_advisors = n_advisors
        self.scenario, self.seed, self.policy, self.difficulty = scenario, seed, policy, difficulty
        self.advisor_fn = advisor_fn
        self.duration = duration_min * 60
        self.store = store
        self.cost_per_advisor_hour = cost_per_advisor_hour
        # two agents: one makes the arrivals, one talks (keeps arrivals identical across policies)
        self.generator = ClientSimulatorAgent(seed=seed, difficulty=difficulty)
        self.requests = self.generator.generate_requests(scenario, duration_min, difficulty=difficulty,
                                                         start_hour=start_hour, realistic=realistic)
        self.incidents = self.generator.incidents
        self.talker = ClientSimulatorAgent(seed=seed + 1, llm=llm, judge_llm=judge_llm, difficulty=difficulty)
        self.talker.crm = self.generator.crm                  # one shared (synthetic) CRM
        self.results: list[dict] = []
        self.transcripts: dict[str, list] = {}
        self.run_id: Optional[str] = None

    def _finish(self, st) -> None:
        self.results.append(self.talker.summary(st))
        self.transcripts[st.request.request_id] = list(st.history)

    def _client(self, req):
        yield self.env.timeout(req.arrival_time)
        arrived = self.env.now
        prio = -req.urgency if self.policy == "priority" else 0   # lower number = served first
        with self.advisors.request(priority=prio) as slot:
            got = yield slot | self.env.timeout(req.patience)
            wait = self.env.now - arrived
            st = self.talker.start(req, wait_seconds=wait)
            if slot not in got:                       # patience exhausted in the queue
                st.status = "abandoned"
                self._finish(st)
                return
            while st.status == "active":
                msg = self.advisor_fn(advisor_view(st, self.generator.crm.get))
                yield self.env.timeout(TURN_SECONDS)
                self.talker.reply(st, msg)
            self._finish(st)

    def run(self) -> list[dict]:
        for req in self.requests:
            self.env.process(self._client(req))
        self.env.run(until=self.duration + 1800)      # let in-flight calls finish
        if self.store is not None:
            self._save()
        return self.results

    def kpis(self, sla_seconds: float = 60) -> dict:
        return kpis(self.results, sla_seconds, n_advisors=self.n_advisors, horizon_s=self.duration,
                    cost_per_advisor_hour=self.cost_per_advisor_hour)

    def _save(self) -> None:
        st = self.store
        self.run_id = st.create_run("simulation", self.scenario, self.policy, self.seed, self.n_advisors,
                                    self.difficulty, {"duration_s": self.duration,
                                                      "cost_per_advisor_hour": self.cost_per_advisor_hour})
        st.upsert_clients(self.generator.crm.values())
        st.save_incidents(self.run_id, self.incidents)
        st.save_calls(self.run_id, self.results, self.transcripts)
        st.save_kpis(self.run_id, self.kpis())


def kpis(results: list[dict], sla_seconds: float = 60, n_advisors: Optional[int] = None,
         horizon_s: Optional[float] = None, cost_per_advisor_hour: float = 12.0) -> dict:
    n = len(results) or 1
    answered = [r for r in results if not r["abandoned"]]
    fraud = [r for r in results if r["is_fraudster"]]
    out = {
        "calls": len(results),
        "abandon_rate": round(sum(r["abandoned"] for r in results) / n, 3),
        "avg_wait_s": round(statistics.mean([r["waited_s"] for r in results]), 1) if results else 0,
        "sla_%": round(100 * sum(r["waited_s"] <= sla_seconds for r in answered) / max(len(answered), 1), 1),
        "avg_satisfaction": round(statistics.mean([r["satisfaction"] for r in results]), 2) if results else 0,
        "resolved_%": round(100 * sum(r["resolved"] for r in results) / n, 1),
        "escalation_%": round(100 * sum(r["escalated"] for r in results) / n, 1),
        "fraud_calls": len(fraud),
        "fraud_leaked": sum(r["resolved"] for r in fraud),   # fraudsters served = bad
        "fraud_leaked_unverified": sum(r["fraud_served_unverified"] for r in fraud),   # served with no valid check
        "fraud_blocked_%": round(100 * sum(not r["resolved"] for r in fraud) / max(len(fraud), 1), 1),
        "resolved_unverified": sum(r["resolved"] and not r["verification_asked"]
                                   and r["intent"] not in LOW_RISK_INTENTS for r in results),  # compliance
        "secret_code_requests": sum(r["sensitive_requests"] for r in results),
    }
    if n_advisors is not None and horizon_s:
        cost = n_advisors * (horizon_s / 3600) * cost_per_advisor_hour
        out["staffing_cost"] = round(cost, 2)
        out["cost_per_resolved"] = round(cost / max(sum(r["resolved"] for r in results), 1), 2)
    return out


if __name__ == "__main__":
    for scenario in ("normal", "fin_de_mois", "panne"):
        print(f"\n== {scenario} (3 advisors, 30 min) ==")
        for policy in ("fifo", "priority"):
            env = CallCenterEnv(n_advisors=3, scenario=scenario, seed=7, policy=policy)
            env.run()
            print(f"{policy:>9}: {env.kpis()}")

    print("\n== curriculum: difficulty grows as the (baseline) agent copes ==")
    cur = Curriculum(start=0.1, end=1.0, target=0.8, step=0.15)
    for episode in range(6):
        env = CallCenterEnv(n_advisors=4, scenario="normal", seed=episode, policy="priority",
                            difficulty=cur.level, duration_min=20)
        env.run()
        k = env.kpis()
        score = 1 - k["abandon_rate"]
        print(f"episode {episode}: difficulty={cur.level:.2f} calls={k['calls']} "
              f"abandon={k['abandon_rate']} -> next level {cur.update(score)}")
