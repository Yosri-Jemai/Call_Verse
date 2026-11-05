"""
Workforce Manager reports: KPIs, staffing per interval, advisors, human reviews, controller decisions.

  compute_kpis(sim, store)   one dict of numbers (reuses sim_env.kpis, adds workforce + approval KPIs)
  write_report(sims, store)  writes report.md / report.json / calls_*.csv / staffing_*.csv / reviews.csv

Fraud KPIs are approval-aware: a fraudster whose request was HELD is only counted as leaked if a
human then approved it; while the review is pending it is counted as "awaiting review".
"""
from __future__ import annotations

import csv
import json
import statistics
from datetime import datetime
from pathlib import Path
from typing import Optional

from sim_env import kpis as base_kpis

from .approvals import CATEGORY_LABELS
from .reviews import APPROVED, PENDING, ReviewStore
from .team import JUNIOR, SENIOR, SKILL_LABELS

DEFAULT_REPORTS_DIR = Path(__file__).resolve().parent / "reports"

HEADLINE = [  # (key, label, better)
    ("calls", "Calls", None),
    ("abandon_rate", "Hang-up rate (abandoned in queue)", "low"),
    ("avg_wait_s", "Average wait (s)", "low"),
    ("sla_%", "Answered within SLA (%)", "high"),
    ("avg_satisfaction", "Satisfaction (1-5)", "high"),
    ("resolved_%", "Closed with action or ticket (%)", "high"),
    ("fraud_calls", "Fraud attempts", None),
    ("fraud_leaked", "Fraud that succeeded", "low"),
    ("fraud_blocked_%", "Fraud blocked (%)", "high"),
    ("fraud_awaiting_review", "Fraud awaiting human decision", None),
    ("actions_held", "Actions held for human approval", None),
    ("genuine_denied", "Genuine requests wrongly denied", "low"),
    ("security_to_senior_%", "Security calls handled by a senior (%)", "high"),
    ("avg_on_duty", "Average advisors on duty", None),
    ("occupancy_%", "Advisor occupancy (%)", None),
    ("staffing_cost", "Staffing cost", "low"),
    ("cost_per_resolved", "Cost per closed call", "low"),
]


def approval_kpis(results: list[dict], store: Optional[ReviewStore], run_id: Optional[str]) -> dict:
    """Fraud and approval numbers. They change when a human decides, so they are recomputed on demand.
    run_id=None means the run had no approval step."""
    fraud = [r for r in results if r["is_fraudster"]]
    statuses = {}
    if store is not None and run_id:
        statuses = {rv["review_id"]: rv["status"] for rv in store.list(run_id=run_id, limit=1_000_000)}

    def executed(r) -> bool:
        """Was the caller's action really carried out?"""
        if r.get("held_for_review"):
            return statuses.get(r["review_id"]) == APPROVED
        return bool(r["resolved"])

    awaiting = [r for r in fraud if r.get("held_for_review") and statuses.get(r["review_id"]) == PENDING]
    leaked = sum(executed(r) for r in fraud)
    out = {
        "fraud_resolved_in_call": sum(bool(r["resolved"]) for r in fraud),   # sim_env's raw number
        "fraud_leaked": leaked,
        "fraud_awaiting_review": len(awaiting),
        "fraud_blocked_%": round(100 * (len(fraud) - leaked - len(awaiting)) / max(len(fraud), 1), 1),
        "fraud_leaked_unverified": sum(executed(r) and r["fraud_served_unverified"] for r in fraud),
        "actions_held": sum(bool(r.get("held_for_review")) for r in results),
        "reviews_created": len(statuses),
    }
    for status in ("pending", "approved", "denied"):
        out[f"reviews_{status}"] = sum(s == status for s in statuses.values())
    reviews = store.list(run_id=run_id, limit=1_000_000) if statuses else []
    out["reviews_by_category"] = {c: sum(rv["category"] == c for rv in reviews)
                                  for c in CATEGORY_LABELS if any(rv["category"] == c for rv in reviews)}
    out["genuine_denied"] = sum(1 for rv in reviews
                                if rv["status"] == "denied" and store.truth(rv["review_id"]) is False)
    return out


def compute_kpis(sim, store: Optional[ReviewStore] = None) -> dict:
    res = sim.results
    out = base_kpis(res, sim.sla_s)
    answered = [r for r in res if not r["abandoned"]]
    out.update(approval_kpis(res, store, sim.run_id if sim.gate is not None else None))

    # workforce
    duration = sim.duration
    cost = sim.staffing_cost()
    closed = sum(r["resolved"] for r in res)
    on_duty_s = sum(a.on_duty_seconds(0, sim.end_time or duration) for a in sim.roster)
    busy_s = sum(a.busy_s for a in sim.roster)
    out["staffing_cost"] = round(cost, 2)
    out["cost_per_resolved"] = round(cost / max(closed, 1), 2)
    out["advisor_hours"] = round(sum(a.on_duty_seconds(0, duration) for a in sim.roster) / 3600, 2)
    out["avg_on_duty"] = round(sim.on_duty_average(0, duration), 2)
    out["avg_seniors_on_duty"] = round(sim.on_duty_average(0, duration, SENIOR), 2)
    out["avg_juniors_on_duty"] = round(sim.on_duty_average(0, duration, JUNIOR), 2)
    out["occupancy_%"] = round(100 * busy_s / max(on_duty_s, 1e-9), 1)
    out["aht_s"] = round(statistics.mean([r["talk_s"] for r in answered]), 1) if answered else 0
    sec = [r for r in answered if r["category"] == "security"]
    out["security_calls"] = len([r for r in res if r["category"] == "security"])
    out["security_to_senior_%"] = round(100 * sum(r["advisor_skill"] == SENIOR for r in sec) / max(len(sec), 1), 1)
    out["overflow_calls"] = sum("overflow" in (r.get("routing") or "") for r in res)
    out["reactive_decisions"] = len(sim.decisions)
    return out


def interval_rows(sim) -> list[dict]:
    rows = []
    fc = {i.index: i for i in (sim.forecast.intervals if getattr(sim, "forecast", None) else [])}
    for iv in sim.plan.intervals:
        start, end = iv.start_s, iv.end_s
        arrived = [r for r in sim.results if start <= r["arrival_s"] < end]
        answered = [r for r in arrived if not r["abandoned"]]
        f = fc.get(iv.index)
        h0 = sim.start_hour + start / 3600
        rows.append(dict(
            interval=f"{int(h0):02d}:{int(round((h0 % 1) * 60)) % 60:02d}",
            forecast_calls=round(f.calls, 1) if f else "",
            actual_calls=len(arrived),
            planned_seniors=iv.seniors, planned_juniors=iv.juniors,
            avg_seniors_on_duty=round(sim.on_duty_average(start, end, SENIOR), 2),
            avg_juniors_on_duty=round(sim.on_duty_average(start, end, JUNIOR), 2),
            expected_sl=iv.expected_sl if iv.expected_sl is not None else "",
            sla_pct=round(100 * sum(r["waited_s"] <= sim.sla_s for r in answered) / max(len(answered), 1), 1),
            abandoned=sum(r["abandoned"] for r in arrived),
            shortage="yes" if iv.shortage else ""))
    return rows


def advisor_rows(sim) -> list[dict]:
    rows = []
    end = sim.end_time or sim.duration
    for a in sim.roster:
        mine = [r for r in sim.results if r.get("advisor") == a.name]
        on = a.on_duty_seconds(0, end)
        rows.append(dict(advisor=a.name, skill=a.skill, calls=len(mine), on_duty_min=round(on / 60, 1),
                         occupancy_pct=round(100 * a.busy_s / on, 1) if on else 0.0,
                         resolved=sum(r["resolved"] for r in mine), escalated=sum(r["escalated"] for r in mine),
                         security_calls=sum(r["category"] == "security" for r in mine),
                         held_for_review=sum(bool(r.get("held_for_review")) for r in mine),
                         fraud_served_in_call=sum(r["is_fraudster"] and r["resolved"] for r in mine)))
    return rows


# ---- writing --------------------------------------------------------------------------
def _fmt(v) -> str:
    if isinstance(v, float):
        return f"{v:g}"
    return "" if v is None else str(v)


def _md_table(rows: list[dict], cols: Optional[list] = None) -> str:
    if not rows:
        return "_none_\n"
    cols = cols or list(rows[0])
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for r in rows:
        lines.append("| " + " | ".join(_fmt(r.get(c)).replace("|", "/").replace("\n", " ") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    cols: list = []
    for r in rows:
        cols += [k for k in r if k not in cols]
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
                        for k, v in r.items()})


def _verdict(key: str, better: Optional[str], base, new) -> str:
    if better is None or not isinstance(base, (int, float)) or not isinstance(new, (int, float)) or base == new:
        return ""
    good = (new < base) if better == "low" else (new > base)
    return "better" if good else "worse"


def write_report(sims: list, store: Optional[ReviewStore] = None, out_dir: Optional[Path] = None,
                 title: str = "Workforce Manager report") -> Path:
    """Write the report for one or more simulations of the same day (first = reference)."""
    first = sims[0]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(out_dir) if out_dir else DEFAULT_REPORTS_DIR / f"{stamp}_{first.scenario}_seed{first.seed}"
    out.mkdir(parents=True, exist_ok=True)

    k = {s.label: compute_kpis(s, store) for s in sims}
    labels = [s.label for s in sims]
    md = [f"# {title}\n",
          f"Scenario **{first.scenario}**, {first.duration / 60:g} min from {first.start_hour:g}h, seed {first.seed}, "
          f"difficulty {first.difficulty:g}. {len(first.requests)} callers, "
          f"{len(first.incidents)} incident(s): "
          + (", ".join(f"{i.name} {int(i.start // 60)}-{int(i.end // 60)} min" for i in first.incidents) or "none")
          + ".\n",
          "All data is synthetic (Client Simulator). Same callers in every column.\n"]

    md.append("## Summary\n")
    rows = []
    for key, label, better in HEADLINE:
        row = {"KPI": label}
        for lb in labels:
            row[lb] = k[lb].get(key, "")
        if len(labels) == 2:
            row["vs " + labels[0]] = _verdict(key, better, k[labels[0]].get(key), k[labels[1]].get(key))
        rows.append(row)
    md.append(_md_table(rows))

    md.append("\n## Setup\n")
    md.append(_md_table([{"run": s.label, "run_id": s.run_id,
                          "routing": s.router.name, "staffing": s.plan.kind,
                          "reactive": "yes" if s.reactive else "no",
                          "human approvals": "yes" if s.gate is not None else "no",
                          "roster": f"{sum(a.skill == SENIOR for a in s.roster)} seniors + "
                                    f"{sum(a.skill == JUNIOR for a in s.roster)} juniors"} for s in sims]))
    md.append(f"\nSkills: {SKILL_LABELS[SENIOR]}; {SKILL_LABELS[JUNIOR]}.\n")

    for s in sims:
        md.append(f"\n## Staffing per interval: {s.label}\n")
        md.append(_md_table(interval_rows(s)))
        md.append(f"\n## Advisors: {s.label}\n")
        md.append(_md_table(advisor_rows(s)))
        if s.decisions:
            md.append(f"\n### Real-time decisions ({len(s.decisions)}): {s.label}\n")
            md.append(_md_table([dict(time=f"{int(d['t'] // 60)} min", decision=d["decision"],
                                      on_duty=f"{d['on_duty'][SENIOR]}S + {d['on_duty'][JUNIOR]}J")
                                 for d in s.decisions]))

    all_reviews = []
    for s in sims:
        if s.gate is not None and store is not None:
            all_reviews += [dict(r, run=s.label) for r in store.list(run_id=s.run_id, limit=1_000_000)]
    md.append("\n## Human reviews\n")
    if all_reviews:
        md.append("Requests the Workforce Manager stopped or flagged. Nothing in a held request is executed "
                  "until a person approves it. Decide pending ones with "
                  "`python -m Workforce_Manager reviews approve|deny <id> --by <name>`.\n\n")
        md.append(_md_table([dict(id=r["review_id"], type=CATEGORY_LABELS.get(r["category"], r["category"]),
                                  client=f"{r['client_name']} ({r['client_id']})", request=r["requested"],
                                  identity=r["identity_check"], risk=r["risk_score"], advisor=r["advisor"],
                                  outcome=r["call_outcome"], status=r["status"], decided_by=r["decided_by"],
                                  note=r["note"]) for r in all_reviews]))
    else:
        md.append("_No review (approval step off, or no sensitive request)._\n")

    md.append("\n## How to read this\n"
              "- **Fraud that succeeded**: a fraudster's request was carried out (served in the call without the "
              "approval step, or approved by a human afterwards).\n"
              "- **Actions held for human approval**: the advisor was about to execute; the Workforce Manager "
              "opened a validation ticket instead.\n"
              "- **Expected SL** is the Erlang-C prediction; **sla_pct** is what the simulation measured.\n"
              "- A **shortage** interval means the roster is too small for the target service level.\n")

    (out / "report.md").write_text("\n".join(md), encoding="utf-8")
    (out / "report.json").write_text(json.dumps({
        "scenario": first.scenario, "seed": first.seed, "duration_min": first.duration / 60,
        "runs": {s.label: dict(run_id=s.run_id, config=getattr(s, "config", {}), kpis=k[s.label],
                               intervals=interval_rows(s), advisors=advisor_rows(s), decisions=s.decisions)
                 for s in sims},
        "forecast": first_forecast_rows(sims),
        "reviews": all_reviews,
    }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    call_cols = ("request_id", "arrival_s", "intent", "category", "profile", "urgency", "advisor", "advisor_skill",
                 "routing", "waited_s", "talk_s", "status", "resolved", "escalated", "abandoned", "held_for_review",
                 "review_id", "satisfaction", "is_fraudster")
    for s in sims:
        urgency = {q.request_id: q.urgency for q in s.requests}
        _write_csv(out / f"calls_{s.label}.csv",
                   [{c: (urgency.get(r["request_id"]) if c == "urgency" else r.get(c)) for c in call_cols}
                    for r in sorted(s.results, key=lambda r: r["arrival_s"])])
        _write_csv(out / f"staffing_{s.label}.csv", interval_rows(s))
    _write_csv(out / "reviews.csv", all_reviews)
    return out


def first_forecast_rows(sims: list) -> list[dict]:
    for s in sims:
        if getattr(s, "forecast", None) is not None:
            return s.forecast.rows()
    return []


def print_summary(sims: list, store: Optional[ReviewStore] = None) -> str:
    """Console table of the headline KPIs."""
    k = {s.label: compute_kpis(s, store) for s in sims}
    labels = [s.label for s in sims]
    w = max(len(lb) for _, lb, _ in HEADLINE)
    lines = [f"{'':<{w}}  " + "  ".join(f"{lb:>12}" for lb in labels)]
    for key, label, _ in HEADLINE:
        lines.append(f"{label:<{w}}  " + "  ".join(f"{_fmt(k[lb].get(key, '')):>12}" for lb in labels))
    text = "\n".join(lines)
    print(text)
    return text
