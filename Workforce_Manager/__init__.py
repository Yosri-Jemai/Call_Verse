"""
Workforce Manager: the agent that organizes the advisors of the call-center Digital Twin.

It decides
  * how many advisors work in each interval (forecast + Erlang-C staffing plan),
  * when to call in extra advisors during the day (reactive control: incidents, spikes),
  * which free advisor takes which caller (skill-based routing),
  * which requests must wait for a HUMAN decision before anything is changed
    (profile changes, sensitive transactions, suspected fraud),
and it writes reports that compare it with the fixed-staff, first-come-first-served setup.

It reuses the Client Simulator (callers, advisors, judge, KPIs) without modifying it.
"""
from __future__ import annotations

import sys
from pathlib import Path

# The Client Simulator modules (client_simulator, sim_env, advisors, ...) are plain top-level modules.
_CLIENT_SIMULATOR = Path(__file__).resolve().parent.parent / "Client_Simulator"
if str(_CLIENT_SIMULATOR) not in sys.path:
    sys.path.insert(0, str(_CLIENT_SIMULATOR))

from .manager import WFMConfig, WorkforceManager, run_baseline  # noqa: E402
from .reviews import ReviewStore  # noqa: E402

__all__ = ["WFMConfig", "WorkforceManager", "run_baseline", "ReviewStore"]
