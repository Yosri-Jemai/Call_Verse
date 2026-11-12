"""Fill a SQLite file with realistic fake QA evaluations to demo the dashboard.

    python seed_demo.py [path/to/db.sqlite3] [count]

Default target is data/quality_reports.sqlite3 (what the API reads without
QA_DATABASE_URL). Use a different path to avoid touching real data.
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage import save_report

GRID_VERSION = "2.0"
WEIGHTS = {"exactitude": .25, "conformite": .25, "resolution": .20, "empathie": .15, "pertinence": .15}
INTENTS = {
    "plafond_carte": 7.8, "frais_bancaires": 7.2, "solde_mouvements": 8.1,
    "fraude_carte": 5.4, "virement_bloque": 6.1, "credit_conso": 6.7,
}
PROFILES = {"calme": 0.8, "pressé": -0.2, "en colère": -0.9, "âgé": -0.4}
LANGS = ["fr", "fr", "fr", "en", "ar"]
ERRORS = [
    ("information_erronee", "majeure", "Quoted value not found in the evidence."),
    ("reponse_sans_source", "majeure", "Figure given without a source or tool result."),
    ("identite_non_verifiee", "critique", "Sensitive tool called without identity verification."),
    ("procedure_non_suivie", "critique", "Secret code requested from the customer."),
    ("ton_inapproprie", "mineure", "Curt tone with an unhappy customer."),
    ("escalade_manquee", "majeure", "No escalation despite the customer's request."),
    ("hallucination", "majeure", "Unsupported banking fact."),
]
RECS = [
    "Always verify the customer's identity before calling an account tool.",
    "Cite the policy source for every limit or fee quoted.",
    "Acknowledge the customer's emotion before proposing a solution.",
    "Offer escalation to a human advisor after two failed resolution attempts.",
    "Never ask for a secret code, even to “confirm”.",
]


def main() -> None:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "data" / "quality_reports.sqlite3"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    rng = random.Random(7)
    now = datetime.now(timezone.utc)
    for n in range(count):
        intent = rng.choice(list(INTENTS))
        profile = rng.choice(list(PROFILES))
        day = now - timedelta(days=rng.random() * 30)
        # slight improvement over time so the trend line has a story
        drift = (30 - (now - day).days) * 0.03
        base = INTENTS[intent] + PROFILES[profile] + drift
        criteria = {}
        for name in WEIGHTS:
            bias = {"empathie": -0.6 if profile == "en colère" else 0.3, "exactitude": -0.4}.get(name, 0)
            criteria[name] = {
                "score": int(max(0, min(10, round(rng.gauss(base + bias, 1.4))))),
                "explanation": "Demo.", "evidence_turns": [1],
            }
        errors = []
        for _ in range(rng.choice([0, 0, 1, 1, 2, 3])):
            kind, sev, desc = rng.choice(ERRORS)
            errors.append({"type": kind, "gravite": sev, "tour": rng.randint(1, 5),
                           "description": desc, "origine": rng.choice(["regle", "llm"])})
        score = round(sum(WEIGHTS[k] * v["score"] for k, v in criteria.items()), 2)
        if any(e["gravite"] == "critique" for e in errors):
            score = min(score, 5)
        call_id = f"demo-{n:04d}"
        checks = [
            {"id": "no_secret_request", "label": "Never ask for authentication secrets (PIN, SMS code)", "passed": not any(e["type"] == "procedure_non_suivie" for e in errors), "turns": []},
            {"id": "identity_before_sensitive_tool", "label": "Verify identity before accessing account data", "passed": not any(e["type"] == "identite_non_verifiee" for e in errors), "turns": []},
            {"id": "numeric_claim_matches_evidence", "label": "Quoted figures match the evidence", "passed": not any(e["type"] == "information_erronee" for e in errors), "turns": []},
        ]
        report = {
            "call_id": call_id, "conversation_id": call_id, "version_grille": GRID_VERSION,
            "scenario": intent, "criteres": criteria, "erreurs": errors, "errors": errors,
            "score_global": score, "overall_score_0_to_100": round(score * 10),
            "explication": "Demo report.",
            "recommendations": rng.sample(RECS, k=rng.randint(1, 3)),
            "avertissements": [{"type": "preuves_outils_absentes", "description": "demo"}] if rng.random() < .2 else [],
            "rule_evaluation": {"checks": checks, "errors": [], "warnings": [], "recommendations": [], "evidence_scope": ""},
            "llm_judgment": {"status": "completed", "provider": "groq", "model": "openai/gpt-oss-120b"},
            "advisor_trace": [],
        }
        case = {
            "call_id": call_id,
            "transcript": [{"turn": 0, "role": "client", "text": "Hello"}, {"turn": 1, "role": "conseiller", "text": "Hello"}],
            "context": {
                "source": "demo", "scenario": intent, "intent": intent, "profile": profile,
                "language": rng.choice(LANGS), "status": "closed",
                "resolved": rng.random() < (0.35 + score / 14), "escalated": rng.random() < 0.15,
                "waited_s": round(rng.uniform(5, 90), 1), "duration_s": round(rng.uniform(60, 420), 1),
            },
        }
        save_report(report, target, case=case)
        # back-date the evaluation so the trend chart spans 30 days
        import sqlite3
        with sqlite3.connect(target) as con:
            con.execute("UPDATE qa_evaluations SET cree_le=? WHERE conversation_id=?", (day.isoformat(), call_id))
    print(f"Seeded {count} demo evaluations into {target}")


if __name__ == "__main__":
    main()
