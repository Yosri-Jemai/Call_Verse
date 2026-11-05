"""
Workforce Manager API (FastAPI), used by the Relay dashboard page /workforce.

Start it from the repository root with the Client Simulator venv:
    .\\Client_Simulator\\.venv\\Scripts\\python.exe -m uvicorn Workforce_Manager.api:app --host 127.0.0.1 --port 8002

  GET  /api/health
  GET  /api/scenarios
  POST /api/plan                      forecast + Erlang-C staffing plan
  POST /api/runs                      run "today" vs the Workforce Manager on the same callers
  GET  /api/runs                      past runs
  GET  /api/runs/{id}                 one run (approval KPIs recomputed from the current decisions)
  GET  /api/runs/{id}/report.md       the report file written when the run finished
  GET  /api/reviews                   human review queue (?status=pending|approved|denied|all&source=all|live|simulation)
  GET  /api/reviews/{id}              one review + audit trail + the conversation
  POST /api/reviews/{id}/decision     approve / deny (name required, final)
  GET  /api/review-stats
  POST /api/live/check                live Relay call: hold a sensitive action before it is executed
  POST /api/live/flag                 live Relay call: a person flags the call for review

Local demo only: no sign-in, synthetic data. Do not expose it on a network.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from . import WFMConfig, WorkforceManager, run_baseline  # noqa: F401  (sets up the Client Simulator path)
from client_simulator import SCENARIOS  # noqa: E402
from judge import rule_analyse  # noqa: E402

from .approvals import CATEGORY_LABELS, FRAUD_SUSPICION, assess, callback_supervisor  # noqa: E402
from .learned import learned_from_conversations  # noqa: E402
from .reports import approval_kpis, advisor_rows, compute_kpis, interval_rows, write_report  # noqa: E402
from .reviews import DEFAULT_DB, PENDING, ReviewStore  # noqa: E402
from .dataset import is_dataset_seed  # noqa: E402
from .model import cached_model  # noqa: E402
from .staffing import team_check  # noqa: E402
from .team import JUNIOR, SENIOR  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"
RUNS_DIR = DATA_DIR / "runs"
LIVE_DIR = DATA_DIR / "live"
LIVE_RUN_ID = "live"

STORE = ReviewStore(os.getenv("WFM_REVIEW_DB", str(DEFAULT_DB)))
RUN_LOCK = threading.Lock()                 # one simulation at a time
LIVE_LOCK = threading.Lock()

app = FastAPI(title="Workforce Manager API", version="1.0")
_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000")
app.add_middleware(CORSMiddleware, allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
                   allow_methods=["*"], allow_headers=["*"])

# Fields of a call kept on disk (server side only: is_fraudster is never sent to the page).
CALL_FIELDS = ("request_id", "client_id", "arrival_s", "intent", "category", "profile", "language", "advisor",
               "advisor_skill", "routing", "waited_s", "talk_s", "status", "resolved", "escalated", "abandoned",
               "held_for_review", "review_id", "review_category", "satisfaction", "is_fraudster",
               "fraud_served_unverified")
INTENT_LABELS = {
    "carte_bloquee": "Blocked card", "prelevement_conteste": "Disputed direct debit", "virement_retard": "Late transfer",
    "dossier_credit": "Loan application", "frais_bancaires": "Bank fees", "plafond_carte": "Card limit question",
    "solde_mouvements": "Balance & transactions", "reclamation": "Complaint", "fraude_signalee": "Fraud reported",
    "changement_coordonnees": "Change contact details", "virement_urgent": "Urgent transfer",
    "hausse_plafond": "Raise card limit", "contestation_carte": "Disputed card payment",
    "vishing_otp": "Vishing / SMS code given", "virement_sans_verification": "Transfer without verification",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def _run_path(key: str) -> Path:
    if not key.replace("-", "").isalnum():
        raise HTTPException(400, "invalid run id")
    return RUNS_DIR / f"{key}.json"


def _sim_run_index() -> dict:
    """simulation run_id (as stored on reviews) -> (run key, label)."""
    out = {}
    for p in RUNS_DIR.glob("*.json"):
        d = _read_json(p)
        if d:
            for label, r in d.get("runs", {}).items():
                out[r["run_id"]] = (d["id"], label)
    return out


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "service": "workforce_manager", "reviews_db": STORE.path,
            "pending_reviews": sum(_actionable(r) for r in STORE.list(status=PENDING, run_id=LIVE_RUN_ID,
                                                                      limit=100_000))}


@app.get("/api/scenarios")
def scenarios():
    return [{"name": n, "calls_per_min": s.calls_per_min, "incident_rate": s.incident_rate,
             "description": s.description} for n, s in SCENARIOS.items()]


# --------------------------------------------------------------------------
# Planning and simulation
# --------------------------------------------------------------------------
class DayBody(BaseModel):
    scenario: str = "fin_de_mois"
    duration_min: float = Field(240, ge=15, le=720)
    seed: int = 7
    start_hour: float = Field(8.0, ge=0, le=23)
    difficulty: float = Field(0.0, ge=0, le=1)
    seniors: int = Field(4, ge=1, le=30)            # roster available to the Workforce Manager
    juniors: int = Field(8, ge=0, le=60)
    routing: Literal["skills", "priority", "fifo"] = "skills"
    overflow_s: float = Field(120, ge=0, le=3600)    # 0 = never send a security call to a junior
    reactive: bool = True
    approvals: bool = True
    sla_s: float = Field(60, ge=10, le=600)
    sla_target: float = Field(0.8, ge=0.5, le=0.99)
    interval_min: float = Field(15, ge=5, le=60)


class RunBody(DayBody):
    fixed_seniors: int = Field(2, ge=0, le=30)      # today's team
    fixed_juniors: int = Field(3, ge=0, le=60)
    review_mode: Literal["manual", "auto"] = "manual"   # manual = the reviews wait for a person in the dashboard


def _config(b: DayBody, history_days: int = 5) -> WFMConfig:
    return WFMConfig(roster_seniors=b.seniors, roster_juniors=b.juniors, routing=b.routing,
                     overflow_s=b.overflow_s or None, reactive=b.reactive, approvals=b.approvals, sla_s=b.sla_s,
                     sla_target=b.sla_target, interval_min=b.interval_min, history_days=history_days)


def _check_scenario(name: str) -> None:
    if name not in SCENARIOS:
        raise HTTPException(400, f"unknown scenario '{name}'")


def _clock(start_hour: float, seconds: float) -> str:
    h = start_hour + seconds / 3600
    return f"{int(h) % 24:02d}:{int(round((h % 1) * 60)) % 60:02d}"


@app.get("/api/learned")
def learned():
    """What was measured from the project's live conversations (shown on the page)."""
    data = learned_from_conversations()
    pending_calls = {r["call_id"] for r in STORE.list(status=PENDING, run_id=LIVE_RUN_ID, limit=100_000)}
    reviews = {r["call_id"]: r for r in STORE.list(run_id=LIVE_RUN_ID, limit=100_000)}
    rows = []
    for r in data.pop("rows"):
        rv = reviews.get(r["call_id"])
        rows.append({
            "call_id": r["call_id"], "when": r["created_at"], "reason": INTENT_LABELS.get(r["intent"], r["intent"]),
            "risky": r["category"] == "security", "exchanges": r["turns"], "minutes": round((r["turns"] or 0) * data["seconds_per_exchange"] / 60, 1),
            "outcome": r["status"], "satisfaction": r["satisfaction"],
            # the simulator's truth is shown only once no human decision is pending for that call
            "thief": None if r["call_id"] in pending_calls else r["is_fraudster"],
            "human_decision": (rv["status"] if rv else None),
        })
    data["rows"] = rows
    data["intents"] = {INTENT_LABELS.get(k, k): v for k, v in sorted(data["intents"].items(), key=lambda kv: -kv[1])}
    return data


@app.post("/api/plan")
def plan(body: DayBody):
    _check_scenario(body.scenario)
    measured = learned_from_conversations()
    if not measured["conversations"] or not measured["handle_s"]:
        raise HTTPException(409, "No finished conversation yet. Run a few calls on the conversation page first: "
                                 "the Workforce Manager measures how long calls last from them.")
    cfg = _config(body)
    cfg.aht_senior_s, cfg.aht_junior_s = measured["handle_risky_s"], measured["handle_simple_s"]
    wfm = WorkforceManager(cfg)
    fc = wfm.forecast(body.scenario, body.duration_min, difficulty=body.difficulty, start_hour=body.start_hour)
    p = wfm.plan(fc, body.duration_min)
    return {"scenario": body.scenario, "history_days": fc.history_days, "forecast_calls": round(fc.total_calls, 1),
            "security_share": round(fc.security_share, 3),
            "sources": {
                "calls_per_hour": f"Client Simulator load model (load_model.py, scenario '{body.scenario}'), "
                                  f"averaged over {fc.history_days} generated days",
                "call_length": f"measured from {measured['conversations']} live conversation(s): risky calls "
                               f"{measured['handle_risky_s']:.0f} s, simple calls {measured['handle_simple_s']:.0f} s "
                               f"({measured['seconds_per_exchange']} s per exchange, the simulator's clock)",
                "risky_share": f"{100 * fc.security_share:.0f}% of the forecast calls are risky (fraud, money, "
                               "personal data): they need a senior",
                "goal": f"{100 * body.sla_target:.0f}% of callers answered within {body.sla_s:.0f} s "
                        "(call-center industry standard), advisors at most 85% busy",
                "team": f"{body.seniors} seniors and {body.juniors} juniors available (your input)",
                "method": "Erlang-C formula (standard call-center staffing method)",
            },
            "measured": {k: measured[k] for k in ("conversations", "handle_risky_s", "handle_simple_s",
                                                  "risky_measured", "simple_measured")},
            "intervals": [dict(time=_clock(body.start_hour, iv.start_s), forecast_calls=iv.forecast_calls,
                               forecast_security=iv.forecast_security, seniors=iv.seniors, juniors=iv.juniors,
                               needed_seniors=iv.needed_seniors, needed_total=iv.needed_total,
                               expected_sl=iv.expected_sl, shortage=iv.shortage) for iv in p.intervals]}


def _setup(sim) -> dict:
    return {"routing": sim.router.name, "staffing": sim.plan.kind, "reactive": sim.reactive is not None,
            "approvals": sim.gate is not None,
            "roster_seniors": sum(a.skill == SENIOR for a in sim.roster),
            "roster_juniors": sum(a.skill == JUNIOR for a in sim.roster)}


def _pack(sim) -> dict:
    gated = sim.gate is not None
    return {
        "run_id": sim.run_id, "label": sim.label, "gated": gated, "setup": _setup(sim),
        "config": getattr(sim, "config", {}), "kpis": compute_kpis(sim, STORE),
        "intervals": interval_rows(sim), "advisors": advisor_rows(sim), "decisions": sim.decisions,
        "calls": [{k: r.get(k) for k in CALL_FIELDS} for r in sim.results],
        # transcripts are kept for the runs with an approval step (the reviewer reads them)
        "transcripts": ({rid: [list(m) for m in h] for rid, h in sim.transcripts.items()} if gated else {}),
    }


@app.post("/api/runs")
def create_run(body: RunBody):
    _check_scenario(body.scenario)
    if body.fixed_seniors + body.fixed_juniors < 1:
        raise HTTPException(400, "today's team needs at least one advisor")
    day = dict(duration_min=body.duration_min, seed=body.seed, difficulty=body.difficulty,
               start_hour=body.start_hour)
    with RUN_LOCK:
        base = run_baseline(body.scenario, seniors=body.fixed_seniors, juniors=body.fixed_juniors,
                            sla_s=body.sla_s, interval_min=body.interval_min, **day)
        wfm = WorkforceManager(_config(body), STORE)
        sim = wfm.simulate(body.scenario, auto_review=body.review_mode == "auto", **day)
        key = "run-" + uuid.uuid4().hex[:10]
        try:
            report_dir = write_report([base, sim], STORE, title=f"Workforce Manager vs today: {body.scenario}")
        except OSError:
            report_dir = None
        data = {
            "id": key, "created_at": _now(), "scenario": body.scenario, "seed": body.seed,
            "duration_min": body.duration_min, "start_hour": body.start_hour, "difficulty": body.difficulty,
            "review_mode": body.review_mode, "settings": body.model_dump(),
            "callers": len(sim.requests),
            "incidents": [dict(name=i.name, start=_clock(body.start_hour, i.start), end=_clock(body.start_hour, i.end),
                               mult=i.mult) for i in sim.incidents],
            "forecast": sim.forecast.rows() if sim.forecast else [],
            "report_dir": str(report_dir) if report_dir else None,
            "runs": {"today": _pack(base), "wfm": _pack(sim)},
        }
        _write_json(_run_path(key), data)
    return _public_run(data)


def _public_run(data: dict) -> dict:
    """What the page receives: no per-call ground truth, approval KPIs refreshed from the decisions."""
    runs = {}
    for label, r in data["runs"].items():
        kpis = dict(r["kpis"])
        if r.get("gated"):
            kpis.update(approval_kpis(r["calls"], STORE, r["run_id"]))
        runs[label] = {k: r[k] for k in ("run_id", "label", "gated", "setup", "intervals", "advisors", "decisions")}
        runs[label]["kpis"] = kpis
    pending = sum(1 for r in data["runs"].values() if r.get("gated")
                  for rv in STORE.list(status=PENDING, run_id=r["run_id"], limit=100_000) if _actionable(rv))
    return {k: data[k] for k in ("id", "created_at", "scenario", "seed", "duration_min", "start_hour", "difficulty",
                                 "review_mode", "settings", "callers", "incidents", "forecast")} | {
        "runs": runs, "pending_reviews": pending, "has_report": bool(data.get("report_dir"))}


@app.get("/api/runs")
def list_runs():
    out = []
    for p in sorted(RUNS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:50]:
        d = _read_json(p)
        if not d:
            continue
        pub = _public_run(d)
        out.append({k: pub[k] for k in ("id", "created_at", "scenario", "seed", "duration_min", "review_mode",
                                         "callers", "pending_reviews")}
                   | {"headline": {lb: {k: r["kpis"].get(k) for k in
                                        ("fraud_leaked", "abandon_rate", "avg_wait_s", "sla_%", "staffing_cost")}
                                   for lb, r in pub["runs"].items()}})
    return out


@app.get("/api/runs/{key}")
def get_run(key: str):
    d = _read_json(_run_path(key))
    if not d:
        raise HTTPException(404, "run not found")
    return _public_run(d)


@app.get("/api/runs/{key}/report.md", response_class=PlainTextResponse)
def get_report(key: str):
    d = _read_json(_run_path(key))
    if not d or not d.get("report_dir"):
        raise HTTPException(404, "report not found")
    path = Path(d["report_dir"]) / "report.md"
    if not path.exists():
        raise HTTPException(404, "report file was deleted")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")


# --------------------------------------------------------------------------
# Human review queue
# --------------------------------------------------------------------------
def _source(rv: dict) -> str:
    return "live" if rv.get("run_id") == LIVE_RUN_ID else "simulation"


LIVE_STOPPED = "stopped during the live call, waiting for a person"


def _actionable(rv: dict) -> bool:
    outcome = rv.get("call_outcome") or ""
    return outcome.startswith(("action held", "flagged manually", "stopped during the live call"))


def _public_review(rv: dict, index: Optional[dict] = None) -> dict:
    out = dict(rv)
    out["source"] = _source(rv)
    out["category_label"] = CATEGORY_LABELS.get(rv["category"], rv["category"])
    out["intent_label"] = INTENT_LABELS.get(rv.get("intent") or "", (rv.get("intent") or "").replace("_", " "))
    if index is not None and out["source"] == "simulation":
        key = index.get(rv.get("run_id"))
        out["run_key"] = key[0] if key else None
    return out


@app.get("/api/reviews")
def list_reviews(status: Literal["pending", "approved", "denied", "all"] = "pending",
                 source: Literal["all", "live", "simulation"] = "all", run_id: Optional[str] = None,
                 actionable: bool = True, limit: int = 300, show_hidden: bool = False):
    rows = STORE.list(status=None if status == "all" else status, run_id=run_id, limit=100_000)
    rows = [r for r in rows if source == "all" or _source(r) == source]
    if not show_hidden:
        hidden = STORE.hidden_ids()
        rows = [r for r in rows if r["review_id"] not in hidden]
    if actionable:   # only requests that were stopped and wait for a person (older info-only entries hidden)
        rows = [r for r in rows if _actionable(r)]
    if status == "pending":      # queue: held actions first, then the riskiest, then the oldest
        rows.sort(key=lambda r: (not r["blocking"], -(r["risk_score"] or 0), r["created_at"]))
    else:                        # history: newest first
        rows.sort(key=lambda r: r["created_at"], reverse=True)
    index = _sim_run_index()
    return [_public_review(r, index) for r in rows[:max(1, min(limit, 2000))]]


class ClearBody(BaseModel):
    by: str = ""
    source: Literal["all", "live", "simulation"] = "live"


@app.post("/api/reviews/clear-history")
def clear_history(body: ClearBody):
    """Hide every decided review of this source from the page (pending ones stay; the audit trail is kept)."""
    rows = [r for r in STORE.list(limit=1_000_000) if r["status"] != PENDING
            and (body.source == "all" or _source(r) == body.source)]
    return {"cleared": STORE.hide_decided([r["review_id"] for r in rows], body.by)}


def _transcript(rv: dict) -> list:
    if _source(rv) == "live":
        d = _read_json(LIVE_DIR / f"{rv['call_id']}.json") if rv.get("call_id", "").isalnum() else None
        return d.get("history", []) if d else []
    key = _sim_run_index().get(rv.get("run_id"))
    if not key:
        return []
    d = _read_json(_run_path(key[0]))
    hist = (d or {}).get("runs", {}).get(key[1], {}).get("transcripts", {}).get(rv.get("call_id"), [])
    return [{"role": m[0], "text": m[1]} for m in hist]


@app.get("/api/reviews/{review_id}")
def get_review(review_id: str):
    rv = STORE.get(review_id)
    if rv is None:
        raise HTTPException(404, "review not found")
    out = _public_review(rv, _sim_run_index())
    out["audit"] = STORE.audit(review_id)
    out["transcript"] = _transcript(rv)
    # Simulation only, and only AFTER the decision: reveal who the caller really was, to check the decision.
    truth = STORE.truth(review_id) if rv["status"] != PENDING else None
    # correct = deny an impostor, approve a genuine customer (fraud reports come from genuine victims)
    out["truth"] = None if truth is None else {"is_fraudster": truth,
                                               "decision_correct": (rv["status"] == "denied") == truth}
    return out


class Decision(BaseModel):
    approved: bool
    by: str = Field(min_length=1, max_length=80)
    note: str = Field("", max_length=500)


@app.post("/api/reviews/{review_id}/decision")
def decide(review_id: str, body: Decision):
    try:
        STORE.decide(review_id, body.approved, body.by, body.note)
    except ValueError as e:
        raise HTTPException(409 if "already" in str(e) else 404 if "not found" in str(e) else 400, str(e))
    return get_review(review_id)


@app.get("/api/review-stats")
def review_stats():
    s = STORE.stats()
    s["live_pending"] = len(STORE.list(status=PENDING, run_id=LIVE_RUN_ID, limit=100_000))
    return s


@app.post("/api/runs/{key}/auto-review")
def auto_review(key: str):
    """Let the simulated supervisor (call-back procedure) decide the pending reviews of this run."""
    d = _read_json(_run_path(key))
    if not d:
        raise HTTPException(404, "run not found")
    for r in d["runs"].values():
        if r.get("gated"):
            callback_supervisor(STORE, r["run_id"])
    return _public_run(d)


# --------------------------------------------------------------------------
# Live Relay calls
# --------------------------------------------------------------------------
class LiveClient(BaseModel):
    name: str = ""
    client_id: str = ""
    intent: str = ""
    language: str = "fr"


class LiveMessage(BaseModel):
    role: str
    text: str


class LiveCheck(BaseModel):
    call_id: str = Field(min_length=1, max_length=64)
    client: LiveClient
    history: list[LiveMessage] = Field(default_factory=list)
    advisor_message: str = Field("", max_length=4000)


class LiveFlag(BaseModel):
    call_id: str = Field(min_length=1, max_length=64)
    client: LiveClient
    history: list[LiveMessage] = Field(default_factory=list)
    reason: str = Field("", max_length=500)
    by: str = Field("operator", max_length=80)


def _safe_call_id(call_id: str) -> str:
    cid = "".join(ch for ch in call_id if ch.isalnum())[:64]
    if not cid:
        raise HTTPException(400, "invalid call id")
    return cid


def _save_live(cid: str, client: LiveClient, history: list) -> None:
    _write_json(LIVE_DIR / f"{cid}.json", {"call_id": cid, "client": client.model_dump(), "updated_at": _now(),
                                            "history": history})


class _LiveAdvisor:
    name, skill = "Cashy (live)", "ai"


REFUSALS = {
    "fr": "Je suis désolé, notre service de sécurité a refusé cette demande pour protéger votre compte. "
          "Aucune modification n'a été faite sur votre dossier.",
    "en": "I am sorry, our security team refused this request to protect your account. "
          "Nothing was changed on your file.",
    "darija": "Sama7ni, service el sécurité rfadh talbek bech na7miw compte mte3ek. 7atta 7aja ma tbaddlet.",
}


@app.post("/api/live/check")
def live_check(body: LiveCheck):
    """Called by the Relay page with each advisor reply BEFORE it reaches the caller.

    pause    = a risky request (profile change, money transfer, suspected fraud) was detected: a review is
               filed and the conversation must wait until a person approves or denies it
    wait     = the review is still pending (keep waiting)
    approved = a person approved: the advisor's reply goes through, the conversation continues
    denied   = a person denied: the reply is replaced by a refusal (`replace_with`)
    continue = the review was denied, but this advisor reply does not claim to execute the action
    none     = nothing risky"""
    cid = _safe_call_id(body.call_id)
    history = [m.model_dump() for m in body.history]
    with LIVE_LOCK:
        _save_live(cid, body.client, history + ([{"role": "conseiller", "text": body.advisor_message}]
                                               if body.advisor_message else []))
        existing = STORE.list(run_id=LIVE_RUN_ID, call_id=cid, limit=1)
        a = assess(body.client.intent, [(m.role, m.text) for m in body.history], None, "")
        info = {"category_label": CATEGORY_LABELS.get(a.category, ""), "requested": a.requested}
        if existing:
            rv = existing[0]
            action = {"pending": "wait", "approved": "approved", "denied": "denied"}[rv["status"]]
            if action == "denied" and body.advisor_message:
                reply_flags = rule_analyse(body.advisor_message)
                if not reply_flags["resolution"] or reply_flags["escalade"]:
                    action = "continue"
            out = {"action": action, "review": _public_review(rv), **info}
            if action == "denied":
                out["replace_with"] = REFUSALS.get(body.client.language, REFUSALS["fr"])
            return out
        if not a.blocking:
            return {"action": "none", "review": None, **info}
        rid = STORE.add(category=a.category, categories=a.categories, blocking=True, run_id=LIVE_RUN_ID,
                        call_id=cid, client_id=body.client.client_id, client_name=body.client.name, fields=a.fields,
                        requested=a.requested, risk_score=a.risk_score, signals=a.signals,
                        identity_check=a.identity_check, intent=body.client.intent, advisor=_LiveAdvisor.name,
                        advisor_skill=_LiveAdvisor.skill, call_outcome=LIVE_STOPPED, evidence=a.evidence[:300])
        return {"action": "pause", "review": _public_review(STORE.get(rid)), **info}


@app.post("/api/live/flag")
def live_flag(body: LiveFlag):
    """A person watching the live call flags it as suspicious: it goes to the review queue."""
    cid = _safe_call_id(body.call_id)
    history = [m.model_dump() for m in body.history]
    with LIVE_LOCK:
        _save_live(cid, body.client, history)
        existing = STORE.list(run_id=LIVE_RUN_ID, call_id=cid, limit=1)
        if existing:
            return {"action": "already_flagged", "review": _public_review(existing[0])}
        a = assess(body.client.intent, [(m.role, m.text) for m in body.history], None, "")
        signals = a.signals + [f"flagged manually by {body.by or 'operator'}" + (f": {body.reason}" if body.reason else "")]
        rid = STORE.add(category=FRAUD_SUSPICION, categories=[FRAUD_SUSPICION] + [c for c in a.categories
                                                                               if c != FRAUD_SUSPICION],
                        blocking=True, run_id=LIVE_RUN_ID, call_id=cid, client_id=body.client.client_id,
                        client_name=body.client.name, fields=a.fields, requested=a.requested,
                        risk_score=a.risk_score, signals=signals, identity_check=a.identity_check,
                        intent=body.client.intent, advisor=_LiveAdvisor.name, advisor_skill=_LiveAdvisor.skill,
                        call_outcome="flagged manually during the live call", evidence=a.evidence[:300])
        return {"action": "flag", "review": _public_review(STORE.get(rid))}


# --------------------------------------------------------------------------
# Control room: one call-center day run by the Workforce Manager, replayed event by event on the page
# --------------------------------------------------------------------------
CONTROL_ROOMS: dict = {}          # id -> (sim, store); the last few days, for reading the conversations
INCIDENT_LABELS = {"panne_application": "Mobile app outage", "panne_distributeur": "ATM outage",
                   "alerte_fraude": "Fraud alert", "campagne_flash": "Flash marketing campaign"}


class ControlBody(BaseModel):
    scenario: str = "fin_de_mois"
    start_hour: float = Field(9, ge=8, le=18)
    duration_min: float = Field(120, ge=30, le=480)
    seniors: int = Field(4, ge=1, le=20)
    juniors: int = Field(6, ge=0, le=30)
    sla_s: float = Field(60, ge=10, le=600)          # service goal: answer ...
    sla_target: float = Field(0.8, ge=0.5, le=0.99)  # ... this share of the callers within sla_s seconds
    seed: Optional[int] = None


def _model():
    try:
        return cached_model()
    except FileNotFoundError as e:
        raise HTTPException(409, str(e))


@app.get("/api/model")
def model_summary():
    """What the Workforce Manager learned from the call log (data/call_log.csv)."""
    return _model().summary()


def gate_evaluation(results: list[dict]) -> dict:
    """The approval check, scored after the day against the simulator's hidden labels (never used to decide)."""
    fraud = [r for r in results if r["is_fraudster"]]
    held = [r for r in results if r.get("held_for_review")]
    held_thieves = sum(r["is_fraudster"] for r in held)
    through = sum(1 for r in fraud if r["resolved"] and not r.get("held_for_review"))
    refused = sum(1 for r in fraud if not r["abandoned"] and not r["resolved"] and not r.get("held_for_review"))
    return dict(thieves=len(fraud), held=len(held), held_thieves=held_thieves, held_genuine=len(held) - held_thieves,
                thieves_through=through, thieves_refused_by_advisor=refused,
                thieves_hung_up=sum(r["abandoned"] for r in fraud),
                precision_pct=round(100 * held_thieves / len(held), 1) if held else None,
                recall_pct=round(100 * held_thieves / (held_thieves + through), 1) if held_thieves + through else None)


@app.post("/api/control-room")
def control_room(body: ControlBody):
    _check_scenario(body.scenario)
    model = _model()
    if body.scenario not in model.slot_mean:
        raise HTTPException(400, f"the call log has no day of type {body.scenario}")
    store = ReviewStore(":memory:")              # the replay's approvals never reach your real approval queue
    seed = body.seed if body.seed is not None else uuid.uuid4().int % 100_000
    while is_dataset_seed(seed):                 # a new day, never one the model learned from
        seed += 1
    wfm = WorkforceManager.from_data(model, body.seniors, body.juniors, body.sla_s, body.sla_target, store)
    cfg = wfm.config
    with RUN_LOCK:
        sim = wfm.simulate(body.scenario, body.duration_min, seed, start_hour=body.start_hour, auto_review=False)
    events = list(sim.events)
    info = {q.request_id: q for q in sim.requests}
    for e in events:
        if e["kind"] == "arrive":
            e["label"] = INTENT_LABELS.get(e["intent"], e["intent"].replace("_", " "))
    for inc in sim.incidents:
        label = INCIDENT_LABELS.get(inc.name, inc.name)
        events.append(dict(t=inc.start, kind="incident", label=label, mult=inc.mult, on=True))
        events.append(dict(t=inc.end, kind="incident", label=label, on=False))
    order = {"incident": 0, "plan": 1, "decision": 2, "alert": 2, "on": 3, "stay": 3, "leaving": 3, "off": 4,
             "arrive": 5, "offer": 5.5, "assign": 6, "callback": 6, "end": 7, "abandon": 7}
    events.sort(key=lambda e: (e["t"], order.get(e["kind"], 9)))
    key = "room-" + uuid.uuid4().hex[:8]
    CONTROL_ROOMS[key] = (sim, store)
    while len(CONTROL_ROOMS) > 5:
        CONTROL_ROOMS.pop(next(iter(CONTROL_ROOMS)))
    k = compute_kpis(sim, store)
    check = team_check(sim.plan, body.seniors, body.juniors, aht_senior_s=cfg.aht_senior_s,
                       aht_junior_s=cfg.aht_junior_s, max_occupancy=cfg.max_occupancy)
    called_back = [r for r in sim.results if r.get("callback_after_s") is not None]
    answered = [r for r in sim.results if not r["abandoned"] and r["status"] != "callback_tomorrow"
                and r.get("callback_after_s") is None]
    gate = gate_evaluation(sim.results)
    test = model.test_error.get(body.scenario, {})
    return {
        "id": key, "scenario": body.scenario, "start_hour": body.start_hour, "duration_s": sim.duration,
        "end_s": max((e["t"] for e in events), default=sim.duration), "seed": seed,
        "team_check": {**check, "aht_senior_s": round(cfg.aht_senior_s), "aht_junior_s": round(cfg.aht_junior_s)},
        "advisors": [{"name": a.name, "skill": a.skill} for a in sim.roster],
        "callers": len(sim.requests), "events": events,
        "goal": {"sla_s": body.sla_s, "sla_target_pct": round(100 * body.sla_target),
                 "reached_pct": round(100 * sum(r["waited_s"] <= body.sla_s for r in answered) / max(len(answered), 1), 1)},
        "summary": {"answered": sum(not r["abandoned"] and r["status"] != "callback_tomorrow" for r in sim.results),
                    "hung_up": sum(r["abandoned"] for r in sim.results),
                    "avg_wait_s": k["avg_wait_s"], "thieves": gate["thieves"], "thieves_succeeded": gate["thieves_through"],
                    "held": k["actions_held"], "advisors_called_in": sum(e["kind"] == "decision" and e["add"] for e in events),
                    "callbacks_offered": sum(e["kind"] == "offer" for e in events),
                    "callbacks_accepted": sum(e["kind"] == "offer" and e["accepted"] for e in events),
                    "called_back": len(called_back),
                    "avg_callback_min": round(sum(r["callback_after_s"] for r in called_back) / len(called_back) / 60, 1)
                    if called_back else 0,
                    "callback_tomorrow": sim.tomorrow,
                    "alerts": sum(e["kind"] == "alert" for e in events)},
        "gate": gate,
        "learned": {
            "train_days": model.train_days.get(body.scenario, 0), "logged_calls": model.calls,
            "aht_senior_s": round(model.aht_senior_s), "aht_junior_s": round(model.aht_junior_s),
            "aht_all_s": round(model.aht_all_s), "aht_n": model.aht_n,
            "patience_median_s": None if model.patience_median_s is None else round(model.patience_median_s),
            "patience_n": model.patience_n, "offer_after_s": round(cfg.offer_callback_after_s),
            "test_day": test.get("day"), "test_total_actual": test.get("total_actual"),
            "test_total_forecast": test.get("total_forecast"), "test_total_error_pct": test.get("total_error_pct"),
            "test_mae": test.get("mae_calls_per_slot"), "test_mean_per_slot": test.get("mean_calls_per_slot"),
        },
        "names": {cid: q.client_name for cid, q in info.items()},
    }


@app.get("/api/control-room/{key}/calls/{call_id}")
def control_room_call(key: str, call_id: str):
    if key not in CONTROL_ROOMS:
        raise HTTPException(404, "this day is no longer in memory: run it again")
    sim, _ = CONTROL_ROOMS[key]
    r = next((x for x in sim.results if x["request_id"] == call_id), None)
    if r is None:
        raise HTTPException(404, "call not found")
    return {"call_id": call_id, "name": next((q.client_name for q in sim.requests if q.request_id == call_id), ""),
            "reason": INTENT_LABELS.get(r["intent"], r["intent"]), "status": r["status"], "advisor": r["advisor"],
            "advisor_skill": r["advisor_skill"], "waited_s": r["waited_s"], "thief": r["is_fraudster"],
            "held": r["held_for_review"],
            "transcript": [{"role": who, "text": text} for who, text in sim.transcripts.get(call_id, [])]}
