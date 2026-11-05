"""
Erlang-C: the standard formula call centers use to turn a call forecast into a head count.

  a = calls_per_second * average_handle_time        (offered load, in "Erlangs")
  C(n, a) = probability that a caller has to wait when n advisors are on duty
  SL(n)   = share of callers answered within `target_s` seconds
          = 1 - C(n, a) * exp(-(n - a) * target_s / aht)

Assumptions (the usual ones): Poisson arrivals, nobody hangs up, every advisor handles every call.
The simulation then checks the plan against the real behaviour (abandons, skills, incidents).
"""
from __future__ import annotations

import math


def erlang_b(n: int, a: float) -> float:
    """Blocking probability with n servers and load a (stable recursive form)."""
    b = 1.0
    for k in range(1, n + 1):
        b = a * b / (k + a * b)
    return b


def erlang_c(n: int, a: float) -> float:
    """Probability that a caller waits (n advisors, a Erlangs)."""
    if a <= 0:
        return 0.0
    if n <= a:
        return 1.0
    b = erlang_b(n, a)
    return n * b / (n - a * (1 - b))


def service_level(n: int, a: float, aht_s: float, target_s: float) -> float:
    """Share of callers answered within target_s seconds."""
    if a <= 0:
        return 1.0
    if n <= a:
        return 0.0
    return 1.0 - erlang_c(n, a) * math.exp(-(n - a) * target_s / aht_s)


def average_speed_of_answer(n: int, a: float, aht_s: float) -> float:
    """Mean waiting time in seconds (inf when the team cannot keep up)."""
    if a <= 0:
        return 0.0
    if n <= a:
        return math.inf
    return erlang_c(n, a) * aht_s / (n - a)


def required_advisors(calls_per_s: float, aht_s: float, target_s: float = 60, sla: float = 0.8,
                      max_occupancy: float = 0.85, minimum: int = 1, cap: int = 500) -> int:
    """Smallest team that reaches the service level without overloading the advisors."""
    a = calls_per_s * aht_s
    if a <= 0:
        return minimum
    n = max(minimum, math.ceil(a))
    while n < cap and (service_level(n, a, aht_s, target_s) < sla or a / n > max_occupancy):
        n += 1
    return n
