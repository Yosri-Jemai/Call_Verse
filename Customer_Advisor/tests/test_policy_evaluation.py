import json
from pathlib import Path

from evaluation.evaluate_policy_quality import (
    DEFAULT_DATASET,
    _run_isolated_model_case,
    load_cases,
    normalize_text,
    score_answer,
    score_retrieval,
)


def test_evaluation_dataset_is_labeled_and_has_answerable_and_unanswerable_cases():
    cases = load_cases(DEFAULT_DATASET)

    assert len(cases) >= 20
    assert any(case["answerable"] for case in cases)
    assert any(not case["answerable"] for case in cases)
    assert len({case["id"] for case in cases}) == len(cases)


def test_normalization_handles_accents_case_and_punctuation():
    assert normalize_text("Plafond: 1 500 TND !") == "plafond 1 500 tnd"
    assert normalize_text("Réclamation") == "reclamation"


def test_retrieval_metrics_measure_rank_and_unanswerable_false_positives():
    cases = [
        {"id": "hit", "answerable": True, "expected_titles": ["Expected"]},
        {"id": "miss", "answerable": True, "expected_titles": ["Missing"]},
        {"id": "abstain", "answerable": False, "expected_titles": []},
        {"id": "false-positive", "answerable": False, "expected_titles": []},
    ]
    results = [
        {
            "id": "hit",
            "found": True,
            "latency_seconds": 0.2,
            "documents": [{"title": "Other"}, {"title": "Expected"}],
        },
        {"id": "miss", "found": True, "latency_seconds": 0.1, "documents": [{"title": "Other"}]},
        {"id": "abstain", "found": False, "latency_seconds": 0.3, "documents": []},
        {"id": "false-positive", "found": True, "latency_seconds": 0.4, "documents": [{"title": "Other"}]},
    ]

    metrics = score_retrieval(cases, results, (1, 2))

    assert metrics["recall_at_k"] == {"1": 0.0, "2": 0.5}
    assert metrics["mrr"] == 0.25
    assert metrics["answerable_retrieval_rate"] == 0.5
    assert metrics["unanswerable_false_positive_rate"] == 0.5
    assert metrics["unanswerable_rejection_rate"] == 0.5
    assert metrics["median_retrieval_latency_seconds"] == 0.25


def test_answer_scoring_checks_all_required_fact_groups_and_abstention():
    case = {
        "required_fact_groups": [["100 TND", "100 dinars"], ["per transaction", "par transaction"]]
    }

    score = score_answer(case, "The limit is 100 TND per transaction.")
    assert score["facts_pass"] is True
    assert score["required_fact_groups_passed"] == 2
    assert score["abstained"] is False

    unsupported = score_answer(
        {"required_fact_groups": []},
        "Je ne peux pas confirmer cette information dans les documents disponibles.",
    )
    assert unsupported["facts_pass"] is None
    assert unsupported["abstained"] is True


def test_dataset_rejects_duplicate_ids_and_answerability_mismatch(tmp_path: Path):
    duplicate_file = tmp_path / "duplicate.jsonl"
    case = {
        "id": "same",
        "question": "Question?",
        "category": "fees",
        "expected_titles": ["Title"],
        "answerable": True,
        "required_fact_groups": [["fact"]],
    }
    duplicate_file.write_text(
        json.dumps(case) + "\n" + json.dumps(case) + "\n",
        encoding="utf-8",
    )
    try:
        load_cases(duplicate_file)
    except ValueError as exc:
        assert "duplicate id" in str(exc)
    else:
        raise AssertionError("Duplicate case IDs must be rejected.")


def test_dataset_rejects_answerability_mismatch(tmp_path: Path):
    dataset = tmp_path / "mismatch.jsonl"
    case = {
        "id": "mismatch",
        "question": "Question?",
        "category": None,
        "expected_titles": ["Title"],
        "answerable": False,
        "required_fact_groups": [],
    }
    dataset.write_text(json.dumps(case) + "\n", encoding="utf-8")

    try:
        load_cases(dataset)
    except ValueError as exc:
        assert "answerable must match" in str(exc)
    else:
        raise AssertionError("Answerability mismatches must be rejected.")


def test_model_evaluation_blocks_non_policy_tool_calls():
    class FakeResponse:
        content = ""
        tool_calls = [{"name": "block_account", "args": {"account_id": "not-used"}}]
        usage_metadata = {}

    class FakeModel:
        calls = 0

        def invoke(self, _messages):
            self.calls += 1
            assert _messages
            return FakeResponse()

    model = FakeModel()
    result = _run_isolated_model_case(model, {"question": "Block my account."})

    assert result["blocked_tool_calls"] == ["block_account"]
    assert result["tool_call_count"] == 0
    assert model.calls == 1


def test_model_evaluation_executes_only_policy_search(monkeypatch):
    import evaluation.evaluate_policy_quality as evaluator

    class FakeResponse:
        def __init__(self, content="", tool_calls=None):
            self.content = content
            self.tool_calls = tool_calls or []
            self.usage_metadata = {"input_tokens": 2, "output_tokens": 1, "total_tokens": 3}

    class FakeModel:
        def __init__(self):
            self.responses = [
                FakeResponse(
                    tool_calls=[
                        {
                            "id": "safe-call",
                            "name": "search_bank_policies",
                            "args": {"query": "contactless limit", "category": "limits"},
                        }
                    ]
                ),
                FakeResponse(content="The limit is 100 TND per transaction."),
            ]

        def invoke(self, _messages):
            assert _messages
            return self.responses.pop(0)

    calls = []

    def fake_search(query, collection, embeddings, *, category, top_k):
        assert collection is not None
        assert embeddings is not None
        calls.append((query, category, top_k))
        return {"found": True}

    monkeypatch.setattr(evaluator, "get_policy_collection", lambda: object())
    monkeypatch.setattr(evaluator, "get_embeddings", lambda: object())
    monkeypatch.setattr(evaluator, "search_policy_collection", fake_search)
    model = FakeModel()

    result = _run_isolated_model_case(model, {"question": "What is the contactless limit?"})

    assert calls == [("contactless limit", "limits", evaluator.TOP_K)]
    assert result["answer"] == "The limit is 100 TND per transaction."
    assert result["tool_call_count"] == 1
    assert result["blocked_tool_calls"] == []
