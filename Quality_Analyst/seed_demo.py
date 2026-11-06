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
    ("information_erronee", "majeure", "Valeur annoncée non retrouvée dans les preuves."),
    ("reponse_sans_source", "majeure", "Chiffre sans source ni résultat d'outil."),
    ("identite_non_verifiee", "critique", "Outil sensible appelé sans vérification d'identité."),
    ("procedure_non_suivie", "critique", "Demande d'un code secret au client."),
    ("ton_inapproprie", "mineure", "Ton sec face à un client mécontent."),
    ("escalade_manquee", "majeure", "Aucune escalade malgré la demande du client."),
    ("hallucination", "majeure", "Fait bancaire non étayé."),
]
RECS = [
    "Toujours vérifier l'identité avant d'appeler un outil de compte.",
    "Citer la source de politique pour chaque plafond ou frais annoncé.",
    "Reformuler l'émotion du client avant de proposer une solution.",
    "Proposer l'escalade vers un humain après deux échecs de résolution.",
    "Ne jamais demander de code secret, même pour « confirmer ».",
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
                "explanation": "Démo.", "evidence_turns": [1],
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
            {"id": "no_secret_request", "label": "Ne pas demander de secret", "passed": not any(e["type"] == "procedure_non_suivie" for e in errors), "turns": []},
            {"id": "identity_before_sensitive_tool", "label": "Vérifier l'identité avant l'accès sensible", "passed": not any(e["type"] == "identite_non_verifiee" for e in errors), "turns": []},
            {"id": "numeric_claim_matches_evidence", "label": "Comparer les chiffres aux preuves", "passed": not any(e["type"] == "information_erronee" for e in errors), "turns": []},
        ]
        report = {
            "call_id": call_id, "conversation_id": call_id, "version_grille": GRID_VERSION,
            "scenario": intent, "criteres": criteria, "erreurs": errors, "errors": errors,
            "score_global": score, "overall_score_0_to_100": round(score * 10),
            "explication": "Rapport de démonstration.",
            "recommendations": rng.sample(RECS, k=rng.randint(1, 3)),
            "avertissements": [{"type": "preuves_outils_absentes", "description": "démo"}] if rng.random() < .2 else [],
            "rule_evaluation": {"checks": checks, "errors": [], "warnings": [], "recommendations": [], "evidence_scope": ""},
            "llm_judgment": {"status": "completed", "provider": "groq", "model": "openai/gpt-oss-120b"},
            "advisor_trace": [],
        }
        case = {
            "call_id": call_id,
            "transcript": [{"turn": 0, "role": "client", "text": "Bonjour"}, {"turn": 1, "role": "conseiller", "text": "Bonjour"}],
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
