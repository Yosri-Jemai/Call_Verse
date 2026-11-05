from __future__ import annotations

import sys
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import httpx
from openai import APIStatusError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyst
from analyst import QualityAnalystError, analyze_case, case_from_simulation, _validate_judgment
from rules import evaluate_rules
from storage import get_report, list_reports, save_conversation, save_report


def make_case() -> dict:
    return {
        "call_id": "test-call-1",
        "transcript": [
            {"turn": 0, "role": "client", "text": "Ma carte est bloquée."},
            {"turn": 1, "role": "conseiller", "text": "Je comprends votre inquiétude."},
        ],
        "context": {
            "scenario": "normal",
            "intent": "carte_bloquee",
            "status": "resolved",
            "resolved": True,
            "is_fraudster": False,
            "advisor_turns": [
                {"turn": 1, "flags": {"empathie": True}},
            ],
        },
    }


def valid_judgment() -> dict:
    return {
        "explication": "Analyse cohérente.",
        "criteres": {
            name: {"score": 8, "explanation": "Étayé.", "evidence_turns": [1]}
            for name in ("exactitude", "conformite", "resolution", "empathie", "pertinence")
        },
        "erreurs": [],
        "recommandations": ["Résumer les prochaines étapes."],
    }


class QualityAnalystTests(TestCase):
    def test_rules_detect_sensitive_data_and_action_before_verification(self):
        case = make_case()
        case["context"]["advisor_turns"] = [
            {
                "turn": 1,
                "flags": {"demande_sensible": True, "resolution": True},
                "advisor": "Pouvez-vous me donner votre code secret ?",
                "identity_verified_before": False,
                "verification_asked_before": False,
            },
        ]
        case["context"]["advisor_trace"] = [
            {"turn": 1, "tools_called": ["get_account_balance"]},
        ]

        with patch.dict("os.environ", {"QA_IDENTITY_REQUIRED_TOOLS": "get_account_balance"}):
            result = evaluate_rules(case)

        self.assertEqual(
            {error["type"] for error in result["errors"]},
            {"procedure_non_suivie", "identite_non_verifiee"},
        )
        self.assertEqual(result["score_0_to_10"], 0)

    def test_security_warning_is_not_misclassified_as_a_secret_request(self):
        case = make_case()
        case["context"]["advisor_turns"] = [
            {
                "turn": 1,
                "flags": {"demande_sensible": True},
                "advisor": "Ne communiquez jamais votre code secret.",
                "identity_verified_before": False,
                "verification_asked_before": False,
            },
        ]

        result = evaluate_rules(case)

        check = next(
            item for item in result["checks"] if item["id"] == "no_secret_request"
        )
        self.assertTrue(check["passed"])

    def test_request_for_full_card_number_is_critical(self):
        case = make_case()
        case["context"]["advisor_turns"] = [
            {
                "turn": 1,
                "flags": {"demande_sensible": True},
                "advisor": "Veuillez me donner le numéro complet de la carte.",
            },
        ]

        result = evaluate_rules(case)

        finding = next(
            item for item in result["errors"] if item["type"] == "procedure_non_suivie"
        )
        self.assertEqual(finding["gravite"], "critique")

    def test_identity_matching_without_an_advisor_check_is_not_verification(self):
        case = make_case()
        case["context"]["advisor_turns"] = [{"turn": 1, "flags": {}}]
        case["context"]["advisor_trace"] = [{"turn": 1, "tools_called": ["update_customer_profile"]}]

        with patch.dict("os.environ", {"QA_IDENTITY_REQUIRED_TOOLS": "update_customer_profile"}):
            result = evaluate_rules(case)

        self.assertIn("identite_non_verifiee", {error["type"] for error in result["errors"]})

    def test_no_llm_report_is_explicitly_partial(self):
        report = analyze_case(make_case(), use_llm=False)

        self.assertEqual(report["llm_judgment"]["status"], "skipped")
        self.assertIsNone(report["score_global"])
        self.assertIsNone(report["overall_score_0_to_100"])

    def test_judgment_rejects_unknown_evidence_turns(self):
        judgment = {
            "explication": "Analyse",
            "criteres": {
                name: {"score": 8, "explanation": "Correct", "evidence_turns": [99]}
                for name in ("exactitude", "conformite", "resolution", "empathie", "pertinence")
            },
            "erreurs": [],
            "recommandations": [],
        }

        with self.assertRaisesRegex(QualityAnalystError, "unknown advisor turn"):
            _validate_judgment(judgment, {0, 1})

    def test_llm_and_rule_scores_are_combined_with_explanations(self):
        judgment = {
            "explication": "Conversation claire.",
            "criteres": {
                name: {"score": 8, "explanation": "Justifié par le dialogue.", "evidence_turns": [1]}
                for name in ("exactitude", "conformite", "resolution", "empathie", "pertinence")
            },
            "erreurs": [],
            "recommandations": ["Résumer les prochaines étapes."],
        }

        def fake_judge(case, *, api_key, model, timeout):
            self.assertEqual(case["call_id"], "test-call-1")
            self.assertEqual(api_key, "test-key")
            self.assertEqual(model, "test-model")
            self.assertGreater(timeout, 0)
            return judgment

        with patch.object(analyst, "_groq_judge", fake_judge):
            report = analyze_case(make_case(), model="test-model", api_key="test-key")

        self.assertEqual(report["llm_judgment"]["status"], "completed")
        self.assertEqual(report["llm_judgment"]["provider"], "groq")
        self.assertEqual(report["score_global"], 8)
        self.assertEqual(report["overall_score_0_to_100"], 80)
        self.assertEqual(report["criteres"]["empathie"]["evidence_turns"], [1])
        self.assertIn("Résumer les prochaines étapes.", report["recommendations"])

    def test_critical_rule_finding_caps_overall_score(self):
        case = make_case()
        case["context"]["advisor_turns"] = [
            {
                "turn": 1,
                "flags": {"demande_sensible": True},
                "advisor": "Veuillez me communiquer votre code secret.",
            },
        ]
        judgment = {
            "explication": "Conversation claire.",
            "criteres": {
                name: {"score": 10, "explanation": "Correct", "evidence_turns": [1]}
                for name in ("exactitude", "conformite", "resolution", "empathie", "pertinence")
            },
            "erreurs": [],
            "recommandations": [],
        }
        with patch.object(analyst, "_groq_judge", return_value=judgment):
            report = analyze_case(case, model="test-model", api_key="test-key")

        self.assertEqual(report["score_global"], 5)
        self.assertEqual(report["overall_score_0_to_100"], 50)
        self.assertEqual(report["score_cap"]["maximum"], 5)

    def test_simulation_adapter_preserves_turn_alignment_and_hidden_context(self):
        state = SimpleNamespace(
            request=SimpleNamespace(
                request_id="sim-call",
                intent="carte_bloquee",
                is_fraudster=False,
                urgency=2,
            ),
            history=[
                ("client", "Ma carte est bloquée."),
                ("conseiller", "Je vais vérifier."),
                ("client", "D'accord."),
                ("conseiller", "La carte est bloquée."),
            ],
            status="resolved",
            resolved=True,
            identity_verified=True,
            verification_asked=True,
            turn_log=[],
        )

        case = case_from_simulation(state, "normal")

        self.assertEqual([turn["turn"] for turn in case["transcript"]], [0, 1, 1, 2])
        self.assertTrue(case["context"]["identity_verified"])
        self.assertEqual(case["context"]["intent"], "carte_bloquee")

    def test_storage_persists_report(self):
        report = {"call_id": "test-call-1", "overall_score_0_to_100": 80}
        with TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite3"
            report_id = save_report(report, database)
            saved = get_report(database, report_id)
            history = list_reports(database)

        self.assertEqual(report_id, 1)
        self.assertEqual(saved["call_id"], "test-call-1")
        self.assertEqual(saved["database_id"], 1)
        self.assertEqual(history[0]["overall_score_0_to_100"], 80)

    def test_storage_persists_conversation_without_an_evaluation(self):
        case = make_case()
        with TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite3"
            save_conversation(case, database, thread_id="thread-only")
            with closing(sqlite3.connect(database)) as connection:
                conversation_count = connection.execute(
                    "SELECT COUNT(*) FROM qa_conversations WHERE call_id = ?",
                    (case["call_id"],),
                ).fetchone()[0]
                turn_count = connection.execute(
                    "SELECT COUNT(*) FROM qa_tours WHERE conversation_id = ?",
                    (case["call_id"],),
                ).fetchone()[0]
                evaluation_count = connection.execute(
                    "SELECT COUNT(*) FROM qa_evaluations WHERE conversation_id = ?",
                    (case["call_id"],),
                ).fetchone()[0]

        self.assertEqual((conversation_count, turn_count, evaluation_count), (1, 2, 0))

    def test_numeric_claim_is_compared_to_same_turn_policy_evidence(self):
        case = make_case()
        case["transcript"][1]["text"] = "Les frais de remplacement sont de 15 TND."
        case["context"]["advisor_trace"] = [{
            "turn": 1,
            "tools_called": ["search_bank_policies"],
            "policy_sources": [{"title": "Tarifs", "text": "Remplacement de carte : 10 TND."}],
            "tool_outputs": [],
        }]

        result = evaluate_rules(case)

        error = next(item for item in result["errors"] if item["type"] == "information_erronee")
        self.assertEqual(error["tour"], 1)
        self.assertEqual(error["gravite"], "majeure")

    def test_numeric_claim_without_evidence_is_not_checked_against_guessed_facts(self):
        case = make_case()
        case["transcript"][1]["text"] = "Le remboursement prend 14 jours."

        result = evaluate_rules(case)

        self.assertFalse(any(
            item["type"] in {"reponse_sans_source", "information_erronee"}
            for item in result["errors"]
        ))
        self.assertTrue(any(item["type"] == "preuves_outils_absentes" for item in result["warnings"]))

    def test_numeric_claim_without_turn_evidence_is_flagged_when_other_evidence_exists(self):
        case = make_case()
        case["transcript"][1]["text"] = "Le remboursement prend 14 jours."
        case["context"]["advisor_trace"] = [{
            "turn": 2,
            "tools_called": ["search_bank_policies"],
            "policy_sources": [{"title": "Politique", "text": "Délai : 10 jours."}],
        }]

        result = evaluate_rules(case)

        error = next(item for item in result["errors"] if item["type"] == "reponse_sans_source")
        self.assertEqual(error["tour"], 1)

    def test_numeric_claim_matches_structured_tool_amount_and_currency(self):
        case = make_case()
        case["transcript"][1]["text"] = "Votre solde est de 25 TND."
        case["context"]["advisor_trace"] = [{
            "turn": 1,
            "tools_called": ["get_account_balance"],
            "tool_calls": [{
                "name": "get_account_balance",
                "result": {"balance": 25, "currency": "TND"},
            }],
            "policy_sources": [],
        }]

        result = evaluate_rules(case)

        self.assertFalse(any(item["type"] == "information_erronee" for item in result["errors"]))
        self.assertFalse(any(item["type"] == "reponse_sans_source" for item in result["errors"]))

    def test_groq_judge_sends_key_and_parses_json_response(self):
        judgment = {
            "explication": "Analyse cohérente.",
            "criteres": {
                name: {"score": 8, "explanation": "Étayé.", "evidence_turns": [1]}
                for name in ("exactitude", "conformite", "resolution", "empathie", "pertinence")
            },
            "erreurs": [],
            "recommandations": ["Résumer les prochaines étapes."],
        }
        requests = []

        class FakeCompletions:
            def create(self, **kwargs):
                requests.append(kwargs)
                return SimpleNamespace(choices=[
                    SimpleNamespace(message=SimpleNamespace(
                        content=json.dumps(judgment, ensure_ascii=False)
                    ))
                ])

        class FakeOpenAI:
            def __init__(self, **kwargs):
                self.chat = SimpleNamespace(completions=FakeCompletions())
                requests.append(kwargs)

        with patch.object(analyst, "OpenAI", FakeOpenAI):
            result = analyst._groq_judge(
                make_case(),
                api_key="test-key",
                model="test-model",
                timeout=9,
            )

        self.assertEqual(requests[0], {
            "api_key": "test-key",
            "base_url": "https://api.groq.com/openai/v1",
            "timeout": 9,
            "max_retries": 0,
        })
        self.assertEqual(requests[1]["model"], "test-model")
        self.assertEqual(requests[1]["response_format"], {"type": "json_object"})
        self.assertEqual(result["explication"], "Analyse cohérente.")

    def test_groq_403_cloudflare_1010_explains_access_denial_not_missing_key(self):
        response = httpx.Response(
            403,
            request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
            text='{"error":"1010"}',
        )
        error = APIStatusError("Forbidden", response=response, body={"error": "1010"})

        class FakeCompletions:
            def create(self, **_kwargs):
                raise error

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        with patch.object(analyst, "OpenAI", FakeOpenAI):
            with self.assertRaisesRegex(
                QualityAnalystError,
                "Cloudflare error 1010.*not reported as a missing API key",
            ):
                analyst._groq_judge(
                    make_case(),
                    api_key="test-key",
                    model="test-model",
                    timeout=9,
                )

    def test_large_prompt_is_compacted_below_configured_token_budget(self):
        case = make_case()
        case["transcript"] = [
            {"turn": turn, "role": role, "text": f"{turn}-{role} " + ("dialogue " * 900)}
            for turn in range(1, 13)
            for role in ("client", "conseiller")
        ]
        case["context"]["advisor_trace"] = [{
            "turn": 1,
            "tools_called": ["search_bank_policies"],
            "tool_outputs": [{"tool": "get_balance", "result": "outil " * 1000}],
            "policy_sources": [
                {"title": f"source-{index}", "text": f"passage-{index} " + ("politique " * 200)}
                for index in range(1, 4)
            ],
        }]
        prompts = []

        class FakeCompletions:
            def create(self, **kwargs):
                prompts.append(kwargs["messages"][0]["content"])
                return SimpleNamespace(choices=[
                    SimpleNamespace(message=SimpleNamespace(
                        content=json.dumps(valid_judgment(), ensure_ascii=False)
                    ))
                ])

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        with patch.dict("os.environ", {
            "QUALITY_ANALYST_MAX_PROMPT_TOKENS": "1500",
            "QUALITY_ANALYST_TOOL_RESULT_CHARS": "300",
            "QUALITY_ANALYST_POLICY_EXCERPT_CHARS": "400",
            "QUALITY_ANALYST_POLICY_PASSES_PER_TURN": "2",
            "QUALITY_ANALYST_FALLBACK_MODELS": "",
        }):
            with patch.object(analyst, "OpenAI", FakeOpenAI):
                analyst._groq_judge(
                    case,
                    api_key="test-key",
                    model="test-model",
                    timeout=9,
                )

        prompt = prompts[0]
        self.assertLessEqual((len(prompt) + 2) // 3, 1500)
        self.assertIn("…[tronqué]", prompt)
        self.assertIn("source-1", prompt)
        self.assertIn("source-2", prompt)
        self.assertNotIn("source-3", prompt)
        self.assertNotIn("1-client", prompt)
        self.assertIn("12-conseiller", prompt)

    def test_413_retries_once_compactly_on_same_model(self):
        case = make_case()
        case["context"]["advisor_trace"] = [{
            "turn": 1,
            "policy_sources": [{"title": "policy", "text": "POLICY " + ("x" * 600)}],
            "tool_outputs": [{"tool": "lookup", "result": "RESULT " + ("y" * 600)}],
        }]
        large_request = APIStatusError(
            "Request too large",
            response=httpx.Response(
                413,
                request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
                text='{"error":{"code":"request_too_large"}}',
            ),
            body={"error": {"code": "request_too_large"}},
        )
        attempts = []

        class FakeCompletions:
            def create(self, **kwargs):
                attempts.append(kwargs)
                if len(attempts) == 1:
                    raise large_request
                return SimpleNamespace(choices=[
                    SimpleNamespace(message=SimpleNamespace(
                        content=json.dumps(valid_judgment(), ensure_ascii=False)
                    ))
                ])

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        with patch.dict("os.environ", {
            "QUALITY_ANALYST_FALLBACK_MODELS": "must-not-be-used",
        }):
            with patch.object(analyst, "OpenAI", FakeOpenAI):
                analyst._groq_judge(
                    case,
                    api_key="test-key",
                    model="same-model",
                    timeout=9,
                )

        self.assertEqual([attempt["model"] for attempt in attempts], ["same-model", "same-model"])
        self.assertEqual([attempt["max_tokens"] for attempt in attempts], [1200, 1200])
        self.assertIn("x" * 380, attempts[0]["messages"][0]["content"])
        self.assertIn("…[tronqué]", attempts[1]["messages"][0]["content"])
        self.assertNotIn("x" * 200, attempts[1]["messages"][0]["content"])

    def test_persistent_413_raises_clear_error_without_trying_fallback_model(self):
        too_large = APIStatusError(
            "Request too large",
            response=httpx.Response(
                413,
                request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
                text='{"error":{"code":"request_too_large"}}',
            ),
            body={"error": {"code": "request_too_large"}},
        )
        models = []

        class FakeCompletions:
            def create(self, **kwargs):
                models.append(kwargs["model"])
                raise too_large

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        with patch.dict("os.environ", {
            "QUALITY_ANALYST_FALLBACK_MODELS": "must-not-be-used",
        }):
            with patch.object(analyst, "OpenAI", FakeOpenAI):
                with self.assertRaisesRegex(QualityAnalystError, "Conversation trop longue.*TPM"):
                    analyst._groq_judge(
                        make_case(),
                        api_key="test-key",
                        model="same-model",
                        timeout=9,
                    )

        self.assertEqual(models, ["same-model", "same-model"])

    def test_429_uses_next_configured_model(self):
        rate_limited = APIStatusError(
            "Rate limit reached",
            response=httpx.Response(
                429,
                request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions"),
                text='{"error":{"code":"rate_limit_exceeded"}}',
            ),
            body={"error": {"code": "rate_limit_exceeded"}},
        )
        models = []

        class FakeCompletions:
            def create(self, **kwargs):
                models.append(kwargs["model"])
                if len(models) == 1:
                    raise rate_limited
                return SimpleNamespace(choices=[
                    SimpleNamespace(message=SimpleNamespace(
                        content=json.dumps(valid_judgment(), ensure_ascii=False)
                    ))
                ])

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        with patch.dict("os.environ", {
            "QUALITY_ANALYST_FALLBACK_MODELS": "fallback-model",
        }):
            with patch.object(analyst, "OpenAI", FakeOpenAI):
                result = analyst._groq_judge(
                    make_case(),
                    api_key="test-key",
                    model="primary-model",
                    timeout=9,
                )

        self.assertEqual(models, ["primary-model", "fallback-model"])
        self.assertEqual(result["_model_used"], "fallback-model")

    def test_truncating_judge_copy_does_not_change_numeric_rule_evidence(self):
        case = make_case()
        case["transcript"][1]["text"] = "Les frais de remplacement sont de 15 TND."
        original_policy = "Remplacement de carte : 10 TND. " + ("preuve " * 100)
        case["context"]["advisor_trace"] = [{
            "turn": 1,
            "tools_called": ["search_bank_policies"],
            "policy_sources": [{"title": "Tarifs", "text": original_policy}],
            "tool_outputs": [{"tool": "search_bank_policies", "result": original_policy}],
        }]
        prompts = []

        class FakeCompletions:
            def create(self, **kwargs):
                prompts.append(kwargs["messages"][0]["content"])
                return SimpleNamespace(choices=[
                    SimpleNamespace(message=SimpleNamespace(
                        content=json.dumps(valid_judgment(), ensure_ascii=False)
                    ))
                ])

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        with patch.dict("os.environ", {"QUALITY_ANALYST_FALLBACK_MODELS": ""}):
            with patch.object(analyst, "OpenAI", FakeOpenAI):
                report = analyst.analyze_case(case, api_key="test-key", model="test-model")

        self.assertIn("information_erronee", {error["type"] for error in report["erreurs"]})
        self.assertEqual(case["context"]["advisor_trace"][0]["policy_sources"][0]["text"], original_policy)
        self.assertIn("…[tronqué]", prompts[0])

    def test_groq_judge_retries_invalid_json_schema_and_includes_feedback(self):
        valid = {
            "explication": "Analyse cohérente.",
            "criteres": {
                name: {"score": 8, "explanation": "Étayé.", "evidence_turns": [1]}
                for name in ("exactitude", "conformite", "resolution", "empathie", "pertinence")
            },
            "erreurs": [],
            "recommandations": [],
        }
        replies = ['{"explication": "incomplet"}', json.dumps(valid, ensure_ascii=False)]
        sent_prompts = []

        class FakeCompletions:
            def create(self, **kwargs):
                sent_prompts.append(kwargs["messages"][0]["content"])
                return SimpleNamespace(choices=[
                    SimpleNamespace(message=SimpleNamespace(content=replies.pop(0)))
                ])

        class FakeOpenAI:
            def __init__(self, **_kwargs):
                self.chat = SimpleNamespace(completions=FakeCompletions())

        with patch.object(analyst, "OpenAI", FakeOpenAI):
            result = analyst._groq_judge(
                make_case(),
                api_key="test-key",
                model="test-model",
                timeout=9,
            )

        self.assertEqual(result["explication"], "Analyse cohérente.")
        self.assertEqual(len(sent_prompts), 2)
        self.assertIn("réponse précédente était invalide", sent_prompts[1])

    def test_storage_persists_conversation_evidence_idempotently_and_links_thread(self):
        case = make_case()
        case["context"].update({
            "source": "simulator_db",
            "scenario": "normal",
            "profile": "standard",
            "intent": "carte_bloquee",
            "language": "fr",
            "status": "resolved",
            "advisor_trace": [{
                "turn": 1,
                "tools_called": ["get_recent_transactions"],
                "tool_calls": [{
                    "id": "tool-1",
                    "name": "get_recent_transactions",
                    "arguments": {"limit": 3},
                    "result": {"transactions": [{"amount": 12, "currency": "TND"}]},
                }],
                "policy_sources": [{"title": "Politique", "text": "Extrait de politique"}],
            }],
        })
        report = {
            "conversation_id": "test-call-1",
            "call_id": "test-call-1",
            "version_grille": "2.0",
            "score_global": 8.5,
            "overall_score_0_to_100": 85,
            "criteres": {},
            "erreurs": [],
            "explication": "Évaluation de test.",
            "recommendations": [],
            "avertissements": [],
            "llm_judgment": {"provider": "groq", "model": "test-model", "status": "completed"},
            "advisor_trace": case["context"]["advisor_trace"],
        }
        with TemporaryDirectory() as directory:
            database = Path(directory) / "reports.sqlite3"
            first_id = save_report(report, database, case=case, thread_id="thread-1", run_id="run-1")
            with closing(sqlite3.connect(database)) as connection:
                counts_before = connection.execute(
                    "SELECT (SELECT COUNT(*) FROM qa_conversations), "
                    "(SELECT COUNT(*) FROM qa_tours), (SELECT COUNT(*) FROM qa_sources), "
                    "(SELECT COUNT(*) FROM qa_liens)"
                ).fetchone()
            second_id = save_report(report, database, case=case, thread_id="thread-1", run_id="run-1")
            with closing(sqlite3.connect(database)) as connection:
                counts_after = connection.execute(
                    "SELECT (SELECT COUNT(*) FROM qa_conversations), "
                    "(SELECT COUNT(*) FROM qa_tours), (SELECT COUNT(*) FROM qa_sources), "
                    "(SELECT COUNT(*) FROM qa_liens)"
                ).fetchone()
                tour_tools = json.loads(connection.execute(
                    "SELECT outils FROM qa_tours WHERE role = 'conseiller'"
                ).fetchone()[0])

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(counts_before, (1, 2, 1, 1))
        self.assertEqual(counts_after, counts_before)
        self.assertEqual(tour_tools[0]["arguments"], {"limit": 3})

    def test_analysis_requires_groq_key_for_llm(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(QualityAnalystError, "Groq API key is missing"):
                analyze_case(make_case())
