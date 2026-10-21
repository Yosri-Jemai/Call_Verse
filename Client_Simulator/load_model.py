"""
Realistic call load for the Digital Twin.

The old generator used a constant Poisson rate. Real call centers are not constant:

  * rate follows the hour of the day (morning and mid-afternoon peaks, lunch dip, closing),
  * random incidents (app outage, ATM outage, fraud alert, flash campaign) create sudden
    spikes that boost specific intents and make clients angrier,
  * a difficulty level in [0, 1] makes everything harsher: more calls, more incidents, less
    patient clients, more fraud. A Curriculum raises it as the RL agent improves.

Arrivals come from a non-homogeneous Poisson process (thinning / Lewis-Shedler), so the
sequence is reproducible for a given seed. Everything is synthetic.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

# Relative call volume by hour of the day (1.0 = average opening hour).
HOURLY_PROFILE = {7: 0.25, 8: 0.7, 9: 1.2, 10: 1.4, 11: 1.2, 12: 0.8, 13: 0.6,
                  14: 1.1, 15: 1.2, 16: 1.0, 17: 0.5, 18: 0.2}
OFF_HOURS = 0.1

INCIDENT_TYPES = {
    "panne_application": dict(weight=3, mult=(2.5, 4.0), minutes=(10, 30), anger=True,
                              boost={"carte_bloquee": 3, "virement_retard": 3, "reclamation": 3}),
    "panne_distributeur": dict(weight=2, mult=(1.8, 2.5), minutes=(15, 40), anger=True,
                               boost={"carte_bloquee": 5}),
    "alerte_fraude": dict(weight=2, mult=(1.5, 2.5), minutes=(10, 25), anger=False, fraud=True,
                          boost={"fraude_signalee": 6, "prelevement_conteste": 3}),
    "campagne_flash": dict(weight=2, mult=(1.5, 2.2), minutes=(20, 45), anger=False,
                           boost={"dossier_credit": 4, "plafond_carte": 2}),
}


@dataclass
class Incident:
    name: str
    start: float                 # seconds since simulation start
    end: float
    mult: float                  # multiplies the call rate while active
    boost: dict = field(default_factory=dict)     # intent -> weight multiplier
    anger: bool = False          # shifts clients toward "insatisfait"
    fraud: bool = False          # shifts clients toward "a_risque"

    def active(self, t: float) -> bool:
        return self.start <= t < self.end


class LoadModel:
    def __init__(self, calls_per_min: float, incident_rate_per_hour: float = 0.3,
                 difficulty: float = 0.0, start_hour: float = 8.0, realistic: bool = True):
        self.difficulty = min(max(difficulty, 0.0), 1.0)
        self.base = calls_per_min * (1 + 0.8 * self.difficulty) / 60.0       # calls per second
        self.incident_rate = incident_rate_per_hour * (1 + 2 * self.difficulty)
        self.start_hour = start_hour
        self.realistic = realistic

    # ---- time of day -------------------------------------------------------
    def hourly(self, t: float) -> float:
        if not self.realistic:
            return 1.0
        hour = int(self.start_hour + t / 3600.0) % 24
        return HOURLY_PROFILE.get(hour, OFF_HOURS)

    def rate(self, t: float, incidents: list[Incident]) -> float:
        r = self.base * self.hourly(t)
        for inc in incidents:
            if inc.active(t):
                r *= inc.mult
        return r

    # ---- incidents ---------------------------------------------------------
    def sample_incidents(self, rng: random.Random, duration_s: float) -> list[Incident]:
        if not self.realistic or self.incident_rate <= 0:
            return []
        out, t = [], 0.0
        names = list(INCIDENT_TYPES)
        weights = [INCIDENT_TYPES[n]["weight"] for n in names]
        while True:
            t += rng.expovariate(self.incident_rate / 3600.0)
            if t >= duration_s:
                break
            name = rng.choices(names, weights)[0]
            spec = INCIDENT_TYPES[name]
            dur = rng.uniform(*spec["minutes"]) * 60
            mult = rng.uniform(*spec["mult"]) * (1 + 0.5 * self.difficulty)
            out.append(Incident(name, round(t, 1), round(t + dur, 1), round(mult, 2),
                                dict(spec["boost"]), spec.get("anger", False), spec.get("fraud", False)))
        return out

    # ---- arrivals (thinning) -----------------------------------------------
    def arrivals(self, rng: random.Random, duration_s: float, incidents: list[Incident]) -> list[float]:
        peak = max(HOURLY_PROFILE.values()) if self.realistic else 1.0
        lam_max = self.base * peak * math.prod(i.mult for i in incidents)
        if lam_max <= 0:
            return []
        t, out = 0.0, []
        while True:
            t += rng.expovariate(lam_max)
            if t > duration_s:
                return out
            if rng.random() <= self.rate(t, incidents) / lam_max:
                out.append(t)


# --------------------------------------------------------------------------
# Curriculum for RL training
# --------------------------------------------------------------------------
@dataclass
class Curriculum:
    """Controls the difficulty level from one training episode to the next.

    linear:   level_at(episode) goes from `start` to `end` over `episodes` episodes.
    adaptive: update(score) raises the level when the agent does well (score >= target),
              lowers it a little when it struggles (score <= target - 0.3).
    `score` is any number in [0, 1], e.g. 1 - abandon_rate, or the share of SLA met.
    """
    start: float = 0.1
    end: float = 1.0
    episodes: int = 100
    target: float = 0.8
    step: float = 0.05
    level: float = 0.1

    def __post_init__(self):
        self.level = self.start

    def level_at(self, episode: int) -> float:
        frac = min(max(episode / max(self.episodes - 1, 1), 0.0), 1.0)
        return round(self.start + (self.end - self.start) * frac, 3)

    def update(self, score: float) -> float:
        if score >= self.target:
            self.level = min(self.end, self.level + self.step)
        elif score <= self.target - 0.3:
            self.level = max(self.start, self.level - self.step / 2)
        return round(self.level, 3)
