"""Aggregated KPIs for the Quality Analyst dashboard.

Reads the latest evaluation of every conversation from the QA database
(SQLite or PostgreSQL) and returns one JSON-serialisable payload. All maths is
done in Python so the same code works on both engines.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from storage import (
    QualityStorageError,
    _DATABASE_ERRORS,
    _connect,
    _ensure_schema,
    _execute,
    _is_postgres,
)

CRITERIA_WEIGHTS = {
    "exactitude": 0.25,
    "conformite": 0.25,
    "resolution": 0.20,
    "empathie": 0.15,
    "pertinence": 0.15,
}
PASS_THRESHOLD = 9.0      # a call "passes" at >= 9/10
FAIL_THRESHOLD = 5.0      # a call is "at risk" below 5/10
SEVERITIES = ("critique", "majeure", "mineure")
BUCKETS = ((0, 2), (2, 4), (4, 6), (6, 8), (8, 10.01))


def _num(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 2) if values else None


def _rate(count: int, total: int) -> float | None:
    return round(100 * count / total, 1) if total else None


def _parse_date(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").replace("Z", "+00:00").replace(" ", "T")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]+", " ", text.casefold()).strip()


def _load_rows(database: str | Path, days: int | None) -> list[dict[str, Any]]:
    """Latest evaluation per conversation, joined with call metadata."""
    if not _is_postgres(database) and not Path(database).exists():
        return []
    try:
        with closing(_connect(database)) as connection:
            with connection:
                _ensure_schema(connection)
                rows = _execute(
                    connection,
                    """
                    SELECT e.evaluation_id, e.conversation_id, e.cree_le, e.modele,
                           e.score_global, e.report_json,
                           c.scenario, c.profil_client, c.motif, c.langue, c.issue,
                           c.temps_attente_s, c.duree_s, c.metadata_json
                    FROM qa_evaluations e
                    JOIN qa_conversations c ON c.conversation_id = e.conversation_id
                    WHERE e.evaluation_id = (
                        SELECT MAX(e2.evaluation_id) FROM qa_evaluations e2
                        WHERE e2.conversation_id = e.conversation_id
                    )
                    ORDER BY e.evaluation_id
                    """,
                ).fetchall()
    except _DATABASE_ERRORS as exc:
        raise QualityStorageError(f"Could not read dashboard data: {exc}") from exc

    cutoff = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    items: list[dict[str, Any]] = []
    for (eid, cid, created, model, score, report_json, scenario, profile, intent,
         language, issue, waited, duration, metadata_json) in rows:
        when = _parse_date(created)
        if cutoff and when and when < cutoff:
            continue
        report = json.loads(report_json)
        metadata = json.loads(metadata_json or "{}")
        items.append({
            "id": eid,
            "conversation_id": cid,
            "date": when,
            "model": model,
            "score": _num(score),
            "report": report,
            "scenario": scenario or report.get("scenario"),
            "profile": profile or "inconnu",
            "intent": intent or "inconnu",
            "language": language or "inconnu",
            "issue": issue,
            "waited": _num(waited),
            "duration": _num(duration),
            "resolved": metadata.get("resolved"),
            "escalated": metadata.get("escalated"),
        })
    return items


def _breakdown(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        groups[str(item[key])].append(item)
    result = []
    for name, group in groups.items():
        scores = [i["score"] for i in group if i["score"] is not None]
        errors = sum(len(i["report"].get("erreurs", [])) for i in group)
        result.append({
            "name": name,
            "calls": len(group),
            "avg_score": _avg(scores),
            "errors_per_call": round(errors / len(group), 2),
            "critical_rate": _rate(
                sum(any(e.get("gravite") == "critique" for e in i["report"].get("erreurs", [])) for i in group),
                len(group),
            ),
        })
    return sorted(result, key=lambda r: (r["avg_score"] is None, r["avg_score"] or 0))


def build_dashboard(database: str | Path, days: int | None = None) -> dict[str, Any]:
    items = _load_rows(database, days)
    scored = [i for i in items if i["score"] is not None]
    scores = [i["score"] for i in scored]
    total = len(items)

    all_errors = [
        {**error, "_call": item["conversation_id"], "_id": item["id"]}
        for item in items for error in item["report"].get("erreurs", [])
    ]
    critical_calls = {e["_call"] for e in all_errors if e.get("gravite") == "critique"}
    resolved_known = [i for i in items if i["resolved"] is not None]
    escalated_known = [i for i in items if i["escalated"] is not None]
    waits = [i["waited"] for i in items if i["waited"] is not None]
    durations = [i["duration"] for i in items if i["duration"] is not None]
    partial = [i for i in items if i["score"] is None]

    # --- trend (per day) + period-over-period delta -----------------------
    per_day: dict[str, list[float]] = defaultdict(list)
    for item in scored:
        if item["date"]:
            per_day[item["date"].date().isoformat()].append(item["score"])
    trend = [{"date": d, "avg_score": _avg(v), "calls": len(v)} for d, v in sorted(per_day.items())]
    delta = None
    dated = [i for i in scored if i["date"]]
    if len(dated) >= 4:
        dated.sort(key=lambda i: i["date"])
        half = len(dated) // 2
        delta = round(_avg([i["score"] for i in dated[half:]]) - _avg([i["score"] for i in dated[:half]]), 2)

    # --- criteria ----------------------------------------------------------
    criteria = []
    for name, weight in CRITERIA_WEIGHTS.items():
        values = [
            _num(i["report"].get("criteres", {}).get(name, {}).get("score"))
            for i in scored
        ]
        values = [v for v in values if v is not None]
        avg = _avg(values)
        criteria.append({
            "name": name,
            "weight": weight,
            "avg_score": avg,
            # weighted points lost vs a perfect 10 = where coaching pays off most
            "points_lost": round(weight * (10 - avg), 2) if avg is not None else None,
            "below_threshold_rate": _rate(sum(v < PASS_THRESHOLD for v in values), len(values)),
        })

    # --- errors -------------------------------------------------------------
    by_type = Counter(e["type"] for e in all_errors)
    by_severity = Counter(e.get("gravite", "mineure") for e in all_errors)
    by_origin = Counter(e.get("origine", "llm") for e in all_errors)
    type_severity: dict[str, Counter] = defaultdict(Counter)
    for e in all_errors:
        type_severity[e["type"]][e.get("gravite", "mineure")] += 1
    errors_by_type = [
        {
            "type": t,
            "count": n,
            "calls_affected": len({e["_call"] for e in all_errors if e["type"] == t}),
            **{s: type_severity[t].get(s, 0) for s in SEVERITIES},
        }
        for t, n in by_type.most_common()
    ]

    # --- rule checks ---------------------------------------------------------
    checks: dict[str, dict[str, Any]] = {}
    for item in items:
        for check in item["report"].get("rule_evaluation", {}).get("checks", []):
            bucket = checks.setdefault(check["id"], {"label": check.get("label", check["id"]), "passed": 0, "failed": 0})
            if check.get("passed") is True:
                bucket["passed"] += 1
            elif check.get("passed") is False:
                bucket["failed"] += 1
    rule_checks = [
        {"id": k, **v, "pass_rate": _rate(v["passed"], v["passed"] + v["failed"])}
        for k, v in checks.items()
    ]
    rule_checks.sort(key=lambda c: (c["pass_rate"] is None, c["pass_rate"] or 0))

    # --- warnings / data-quality of the audit itself -------------------------
    warning_counter = Counter(
        w.get("type", "autre") for i in items for w in i["report"].get("avertissements", [])
    )

    # --- recurring coaching recommendations ----------------------------------
    recs: dict[str, dict[str, Any]] = {}
    for item in items:
        for text in item["report"].get("recommendations", []):
            key = _normalize(text)[:90]
            entry = recs.setdefault(key, {"text": text, "count": 0})
            entry["count"] += 1
    top_recommendations = sorted(recs.values(), key=lambda r: -r["count"])[:8]

    # --- distribution, worst calls ---------------------------------------------
    distribution = [
        {"label": f"{lo}–{min(hi, 10):g}", "count": sum(lo <= s < hi for s in scores)}
        for lo, hi in BUCKETS
    ]
    worst = sorted(scored, key=lambda i: i["score"])[:6]
    worst_calls = [
        {
            "report_id": i["id"],
            "call_id": i["conversation_id"],
            "score": i["score"],
            "intent": i["intent"],
            "profile": i["profile"],
            "errors": [e.get("type") for e in i["report"].get("erreurs", [])][:4],
        }
        for i in worst
    ]

    # --- plain-language insights ---------------------------------------------
    insights: list[dict[str, str]] = []
    usable = [c for c in criteria if c["points_lost"] is not None]
    if usable:
        weakest = max(usable, key=lambda c: c["points_lost"])
        insights.append({
            "level": "action",
            "text": f"Top criterion to coach: {weakest['name']} "
                    f"({weakest['avg_score']}/10, {weakest['points_lost']} points lost on the overall score).",
        })
    if errors_by_type:
        top = errors_by_type[0]
        insights.append({
            "level": "action",
            "text": f"Most frequent error: {top['type']} ({top['count']} occurrences across {top['calls_affected']} calls).",
        })
    if critical_calls:
        insights.append({
            "level": "alert",
            "text": f"{len(critical_calls)} call(s) with a critical error ({_rate(len(critical_calls), total)}%) — score capped at 5/10.",
        })
    weak_intents = [b for b in _breakdown(items, "intent") if b["avg_score"] is not None and b["calls"] >= 3]
    if weak_intents:
        insights.append({
            "level": "info",
            "text": f"Weakest call reason: {weak_intents[0]['name']} ({weak_intents[0]['avg_score']}/10 over {weak_intents[0]['calls']} calls).",
        })
    if partial:
        insights.append({
            "level": "info",
            "text": f"{len(partial)} partial evaluation(s) (rules only, no score) — re-run with the LLM judge.",
        })
    if delta is not None:
        insights.append({
            "level": "info" if delta >= 0 else "alert",
            "text": f"Trend: {'+' if delta >= 0 else ''}{delta} pt between the first and second half of the period.",
        })

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "thresholds": {"pass": PASS_THRESHOLD, "at_risk": FAIL_THRESHOLD},
        "kpis": {
            "total_reviews": total,
            "scored_reviews": len(scored),
            "partial_reviews": len(partial),
            "avg_score": _avg(scores),
            "score_delta": delta,
            "pass_rate": _rate(sum(s >= PASS_THRESHOLD for s in scores), len(scores)),
            "at_risk_rate": _rate(sum(s < FAIL_THRESHOLD for s in scores), len(scores)),
            "critical_call_rate": _rate(len(critical_calls), total),
            "errors_per_call": round(len(all_errors) / total, 2) if total else None,
            "resolution_rate": _rate(sum(bool(i["resolved"]) for i in resolved_known), len(resolved_known)),
            "escalation_rate": _rate(sum(bool(i["escalated"]) for i in escalated_known), len(escalated_known)),
            "avg_wait_s": _avg(waits),
            "avg_duration_s": _avg(durations),
            "rule_error_share": _rate(by_origin.get("regle", 0), len(all_errors)),
        },
        "trend": trend,
        "criteria": criteria,
        "distribution": distribution,
        "errors_by_type": errors_by_type,
        "errors_by_severity": [{"severity": s, "count": by_severity.get(s, 0)} for s in SEVERITIES],
        "rule_checks": rule_checks,
        "warnings": [{"type": t, "count": n} for t, n in warning_counter.most_common(6)],
        "by_intent": _breakdown(items, "intent"),
        "by_profile": _breakdown(items, "profile"),
        "by_language": _breakdown(items, "language"),
        "by_model": _breakdown([i for i in items if i["model"]], "model"),
        "top_recommendations": top_recommendations,
        "worst_calls": worst_calls,
        "timeline": [
            {
                "report_id": i["id"],
                "score": i["score"],
                "intent": i["intent"],
                "date": i["date"].isoformat() if i["date"] else None,
            }
            for i in sorted(scored, key=lambda i: i["id"])
        ][-40:],
        "models": [{"name": n or "règles seules", "count": c} for n, c in Counter(i["model"] for i in items).most_common()],
        "insights": insights,
    }