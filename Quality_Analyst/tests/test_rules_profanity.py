"""Quality Analyst rule for advisor bad language. Run: pytest -q Quality_Analyst/tests/test_rules_profanity.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rules import evaluate_rules  # noqa: E402


def _case(*turns):
    return {
        "transcript": [],
        "context": {"advisor_turns": [
            {"turn": i + 1, "flags": flags} for i, flags in enumerate(turns)
        ]},
    }


def test_clean_turn_passes_professional_language():
    result = evaluate_rules(_case({"grossier": False}))
    check = next(c for c in result["checks"] if c["id"] == "professional_language")
    assert check["passed"] is True
    assert not any(e["type"] == "ton_inapproprie" for e in result["errors"])


def test_mild_is_minor_and_vulgar_is_critical():
    result = evaluate_rules(_case(
        {"grossier": True, "grossier_niveau": 1, "grossier_mots": ["s*****e"]},
        {"grossier": True, "grossier_niveau": 2, "grossier_mots": ["c*****d"]},
    ))
    errors = [e for e in result["errors"] if e["type"] == "ton_inapproprie"]
    assert [e["gravite"] for e in errors] == ["mineure", "critique"]
    assert all("connard" not in e["description"] for e in errors)  # words stay masked
