"""
What the Workforce Manager learns from the project's own conversations.

Source: the Client Simulator database (DATABASE_URL in Client_Simulator/.env). Every live Relay call
(simulated customer <-> Cashy) is saved there when it ends: runs.kind = 'live', table `calls` (intent,
number of exchanges, outcome, ground-truth fraud label) and table `turns` (the transcript).

From those conversations it measures
  * how long a call takes: exchanges x TURN_SECONDS (the simulator's own clock, sim_env.py),
    separately for risky calls (they need a senior) and simple calls,
  * what customers ask for, how many requests were risky, how many callers were thieves,
  * how the calls ended.
The staffing plan uses these measured call lengths instead of fixed values.
"""
from __future__ import annotations

import statistics
from typing import Optional

from sim_env import TURN_SECONDS

from .routing import call_category

_STORE = None
_STORE_ERROR = ""


def project_store():
    """The Client Simulator's Store (PostgreSQL or SQLite), opened once."""
    global _STORE, _STORE_ERROR
    if _STORE is None:
        try:
            from db import Store
            _STORE = Store()
            _STORE.init_schema()
            _STORE_ERROR = ""
        except Exception as e:                     # database down: the page says so, nothing crashes
            _STORE_ERROR = f"{type(e).__name__}: {e}"
            return None
    return _STORE


def live_conversations(limit: int = 1000) -> list[dict]:
    store = project_store()
    if store is None:
        return []
    rows = store._run(
        "SELECT c.call_id, c.client_id, c.intent, c.language, c.profile, c.status, c.turns, c.resolved, "
        "c.escalated, c.abandoned, c.is_fraudster, c.satisfaction, c.identity_verified, r.created_at "
        "FROM calls c JOIN runs r ON r.run_id = c.run_id WHERE r.kind = ? ORDER BY r.created_at DESC LIMIT ?",
        ("live", limit), fetch=True) or []
    for r in rows:
        r["created_at"] = str(r["created_at"])
        r["category"] = call_category(r["intent"] or "")
        for k in ("resolved", "escalated", "abandoned", "is_fraudster", "identity_verified"):
            r[k] = bool(r[k])
    return rows


def _handle_s(rows: list[dict]) -> Optional[float]:
    talked = [r["turns"] * TURN_SECONDS for r in rows if not r["abandoned"] and (r["turns"] or 0) > 0]
    return round(statistics.mean(talked), 1) if talked else None


def learned_from_conversations() -> dict:
    rows = live_conversations()
    risky = [r for r in rows if r["category"] == "security"]
    simple = [r for r in rows if r["category"] != "security"]
    overall = _handle_s(rows)
    thieves = [r for r in rows if r["is_fraudster"]]
    outcomes: dict = {}
    for r in rows:
        outcomes[r["status"]] = outcomes.get(r["status"], 0) + 1
    intents: dict = {}
    for r in rows:
        intents[r["intent"]] = intents.get(r["intent"], 0) + 1
    return {
        "source": "Client Simulator database: live Relay conversations (runs.kind = 'live')",
        "db_error": _STORE_ERROR,
        "conversations": len(rows),
        "seconds_per_exchange": TURN_SECONDS,
        "avg_exchanges": round(statistics.mean([r["turns"] or 0 for r in rows]), 2) if rows else None,
        "handle_s": overall,
        "handle_risky_s": _handle_s(risky) or overall,
        "handle_simple_s": _handle_s(simple) or overall,
        "risky_measured": bool(_handle_s(risky)),
        "simple_measured": bool(_handle_s(simple)),
        "risky_calls": len(risky),
        "risky_share": round(len(risky) / len(rows), 3) if rows else None,
        "thieves": len(thieves),
        "thieves_stopped": sum(not r["resolved"] for r in thieves),
        "outcomes": outcomes,
        "intents": intents,
        "avg_satisfaction": round(statistics.mean([r["satisfaction"] for r in rows if r["satisfaction"] is not None]), 2)
        if any(r["satisfaction"] is not None for r in rows) else None,
        "rows": rows,
    }
