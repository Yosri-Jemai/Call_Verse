"""Evaluate policy retrieval and, optionally, safe model answers against a labeled set."""

from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
import sys
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any

from src.agent.policy_rag import (
    COLLECTION_NAME,
    TOP_K,
    get_embeddings,
    get_policy_collection,
    search_policy_collection,
)
from src.config import settings

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = Path(__file__).with_name("policy_qa.jsonl")
DEFAULT_K_VALUES = (1, 2, 4)
DEFAULT_THRESHOLDS = (0.45, 0.55, 0.65)
ABSTENTION_PHRASES = (
    "cannot confirm",
    "can't confirm",
    "not available in the documents",
    "not found in the available documents",
    "no relevant document",
    "cannot answer from the available",
    "ne peux pas confirmer",
    "je ne peux pas confirmer",
    "aucun document pertinent",
    "pas disponible dans les documents",
    "ma najamch n2akked",
)


def normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    ascii_text = ascii_text.replace("%", " percent ")
    return " ".join(
        "".join(char if char.isalnum() else " " for char in ascii_text).split()
    )


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    seen_ids = set()
    with path.open(encoding="utf-8") as dataset:
        for line_number, line in enumerate(dataset, start=1):
            if not line.strip():
                continue
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON.") from exc
            if not isinstance(case, dict):
                raise ValueError(f"{path}:{line_number}: each case must be a JSON object.")
            required = {
                "id",
                "question",
                "category",
                "expected_titles",
                "answerable",
                "required_fact_groups",
            }
            missing = required - case.keys()
            if missing:
                raise ValueError(
                    f"{path}:{line_number}: missing fields: {', '.join(sorted(missing))}."
                )
            if not isinstance(case["id"], str) or not case["id"].strip():
                raise ValueError(f"{path}:{line_number}: id must be a non-empty string.")
            if case["id"] in seen_ids:
                raise ValueError(f"{path}:{line_number}: duplicate id {case['id']!r}.")
            if not isinstance(case["question"], str) or not case["question"].strip():
                raise ValueError(f"{path}:{line_number}: question must be a non-empty string.")
            if not isinstance(case["expected_titles"], list) or not all(
                isinstance(title, str) and title.strip() for title in case["expected_titles"]
            ):
                raise ValueError(f"{path}:{line_number}: expected_titles must be strings.")
            if not isinstance(case["answerable"], bool):
                raise ValueError(f"{path}:{line_number}: answerable must be a boolean.")
            if not isinstance(case["required_fact_groups"], list) or not all(
                isinstance(group, list)
                and group
                and all(isinstance(term, str) and term.strip() for term in group)
                for group in case["required_fact_groups"]
            ):
                raise ValueError(f"{path}:{line_number}: invalid required_fact_groups.")
            if case["answerable"] != bool(case["expected_titles"]):
                raise ValueError(
                    f"{path}:{line_number}: answerable must match whether expected_titles is empty."
                )
            if case["answerable"] and not case["required_fact_groups"]:
                raise ValueError(
                    f"{path}:{line_number}: answerable cases need at least one fact group."
                )
            seen_ids.add(case["id"])
            cases.append(case)
    if not cases:
        raise ValueError(f"No evaluation cases found in {path}.")
    return cases


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(value, 4)


def score_retrieval(
    cases: list[dict[str, Any]],
    results: list[dict[str, Any]],
    k_values: tuple[int, ...],
) -> dict[str, Any]:
    if len(cases) != len(results):
        raise ValueError("Each evaluation case must have exactly one retrieval result.")
    answerable = [case for case in cases if case["answerable"]]
    unanswerable = [case for case in cases if not case["answerable"]]
    by_id = {result["id"]: result for result in results}
    if len(by_id) != len(results) or set(by_id) != {case["id"] for case in cases}:
        raise ValueError("Retrieval result IDs must match evaluation case IDs exactly.")

    answerable_reciprocal_ranks = []
    for case in answerable:
        result = by_id[case["id"]]
        relevant = set(case["expected_titles"])
        rank = next(
            (
                index
                for index, document in enumerate(result["documents"], start=1)
                if document["title"] in relevant
            ),
            None,
        )
        answerable_reciprocal_ranks.append(1 / rank if rank else 0.0)

    recall_at_k = {}
    for k in k_values:
        hits = 0
        for case in answerable:
            result = by_id[case["id"]]
            if any(
                document["title"] in case["expected_titles"]
                for document in result["documents"][:k]
            ):
                hits += 1
        recall_at_k[str(k)] = round(hits / len(answerable), 4) if answerable else None

    false_positives = sum(bool(by_id[case["id"]]["found"]) for case in unanswerable)
    true_negatives = len(unanswerable) - false_positives
    answerable_hits = sum(
        any(document["title"] in case["expected_titles"] for document in by_id[case["id"]]["documents"])
        for case in answerable
    )
    latencies = [float(result["latency_seconds"]) for result in results]
    return {
        "case_count": len(cases),
        "answerable_count": len(answerable),
        "unanswerable_count": len(unanswerable),
        "recall_at_k": recall_at_k,
        "mrr": round(statistics.mean(answerable_reciprocal_ranks), 4)
        if answerable_reciprocal_ranks
        else None,
        "answerable_retrieval_rate": round(answerable_hits / len(answerable), 4)
        if answerable
        else None,
        "unanswerable_false_positive_rate": round(false_positives / len(unanswerable), 4)
        if unanswerable
        else None,
        "unanswerable_rejection_rate": round(true_negatives / len(unanswerable), 4)
        if unanswerable
        else None,
        "median_retrieval_latency_seconds": _percentile(latencies, 0.5),
        "p95_retrieval_latency_seconds": _percentile(latencies, 0.95),
    }


def score_answer(case: dict[str, Any], answer: str) -> dict[str, Any]:
    normalized_answer = normalize_text(answer)
    fact_results = [
        any(normalize_text(term) in normalized_answer for term in alternatives)
        for alternatives in case["required_fact_groups"]
    ]
    abstained = any(
        normalize_text(phrase) in normalized_answer for phrase in ABSTENTION_PHRASES
    )
    return {
        "required_fact_groups_passed": sum(fact_results),
        "required_fact_groups_total": len(fact_results),
        "facts_pass": all(fact_results) if fact_results else None,
        "abstained": abstained,
    }


def _extract_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


def _execute_policy_search(arguments: dict[str, Any]) -> str:
    query = arguments.get("query")
    category = arguments.get("category")
    if not isinstance(query, str):
        raise ValueError("The model supplied an invalid policy-search query.")
    if category is not None and not isinstance(category, str):
        raise ValueError("The model supplied an invalid policy-search category.")
    result = search_policy_collection(
        query,
        get_policy_collection(),
        get_embeddings(),
        category=category,
        top_k=TOP_K,
    )
    return json.dumps(result, ensure_ascii=False)


def _run_isolated_model_case(model: Any, case: dict[str, Any]) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    from src.agent.prompts import get_system_prompt

    today = date.today()
    system_prompt = get_system_prompt(settings.app_mode).format(
        today=today.isoformat(), year=today.year
    )
    messages = [
        SystemMessage(content=system_prompt),
        SystemMessage(
            content=(
                "Evaluation safety constraint: the only executable tool is "
                "search_bank_policies. Never call any other tool. Do not request or reveal "
                "customer, account, transaction, or credential data."
            )
        ),
        HumanMessage(content=case["question"]),
    ]
    tool_calls = 0
    blocked_tool_calls: list[str] = []
    token_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    started = time.perf_counter()
    for _ in range(4):
        response = model.invoke(messages)
        usage = getattr(response, "usage_metadata", None) or {}
        for key in token_usage:
            token_usage[key] += int(usage.get(key, 0) or 0)
        calls = getattr(response, "tool_calls", []) or []
        if not calls:
            return {
                "answer": _extract_text(response),
                "latency_seconds": round(time.perf_counter() - started, 4),
                "tool_call_count": tool_calls,
                "blocked_tool_calls": blocked_tool_calls,
                "token_usage": token_usage,
            }
        rejected = [
            call.get("name", "unknown")
            for call in calls
            if call.get("name") != "search_bank_policies"
        ]
        if rejected:
            blocked_tool_calls.extend(rejected)
            return {
                "answer": "",
                "latency_seconds": round(time.perf_counter() - started, 4),
                "tool_call_count": tool_calls,
                "blocked_tool_calls": blocked_tool_calls,
                "token_usage": token_usage,
            }
        messages.append(response)
        for call in calls:
            tool_calls += 1
            arguments = call.get("args", {})
            if not isinstance(arguments, dict):
                raise ValueError("The model supplied invalid policy-search arguments.")
            result = _execute_policy_search(arguments)
            messages.append(
                ToolMessage(
                    content=result,
                    tool_call_id=call.get("id", f"evaluation-{tool_calls}"),
                )
            )
    return {
        "answer": "",
        "latency_seconds": round(time.perf_counter() - started, 4),
        "tool_call_count": tool_calls,
        "blocked_tool_calls": blocked_tool_calls,
        "token_usage": token_usage,
        "error": "Model exceeded the four-step evaluation tool limit.",
    }


def evaluate_model(
    cases: list[dict[str, Any]],
    retrieval_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    from src.agent.nodes import create_model

    model = create_model()
    case_by_id = {case["id"]: case for case in cases}
    evaluated = []
    for case in cases:
        answer_result = _run_isolated_model_case(model, case)
        scored = score_answer(case, answer_result["answer"])
        retrieval = retrieval_by_id[case["id"]]
        retrieved_sources = [
            document["source"]
            for document in retrieval["documents"]
            if document["title"] in case["expected_titles"]
        ]
        normalized_answer = normalize_text(answer_result["answer"])
        cited_relevant_source = bool(retrieved_sources) and any(
            normalize_text(source) in normalized_answer for source in retrieved_sources
        )
        evaluated.append(
            {
                "id": case["id"],
                "answer": answer_result["answer"],
                "latency_seconds": answer_result["latency_seconds"],
                "tool_call_count": answer_result["tool_call_count"],
                "blocked_tool_calls": answer_result["blocked_tool_calls"],
                "token_usage": answer_result["token_usage"],
                "error": answer_result.get("error"),
                "facts_pass": scored["facts_pass"],
                "required_fact_groups_passed": scored["required_fact_groups_passed"],
                "required_fact_groups_total": scored["required_fact_groups_total"],
                "abstained": scored["abstained"],
                "relevant_source_cited": cited_relevant_source,
            }
        )

    answerable = [item for item in evaluated if case_by_id[item["id"]]["answerable"]]
    unanswerable = [item for item in evaluated if not case_by_id[item["id"]]["answerable"]]
    fact_scored = [item for item in answerable if item["facts_pass"] is not None]
    citation_eligible = [
        item for item in answerable
        if retrieval_by_id[item["id"]]["documents"]
        and any(
            doc["title"] in case_by_id[item["id"]]["expected_titles"]
            for doc in retrieval_by_id[item["id"]]["documents"]
        )
    ]
    latencies = [item["latency_seconds"] for item in evaluated]
    token_totals = {
        key: sum(item["token_usage"][key] for item in evaluated)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }
    return {
        "provider": settings.resolved_provider or "unconfigured",
        "model": settings.model_name
        or {
            "openai": "gpt-5-mini",
            "anthropic": "claude-sonnet-4-20250514",
            "google": "gemini-3.8-flash",
            "groq": "openai/gpt-oss-120b",
            "huggingface": "meta-llama/Llama-3.3-70B-Instruct",
            "free-tier": "Qwen/Qwen2.5-7B-Instruct",
        }.get(settings.resolved_provider),
        "case_count": len(evaluated),
        "answerable_fact_accuracy": round(
            sum(item["facts_pass"] is True for item in fact_scored) / len(fact_scored), 4
        ) if fact_scored else None,
        "relevant_source_citation_rate": round(
            sum(item["relevant_source_cited"] for item in citation_eligible)
            / len(citation_eligible), 4
        ) if citation_eligible else None,
        "unanswerable_abstention_rate": round(
            sum(item["abstained"] for item in unanswerable) / len(unanswerable), 4
        ) if unanswerable else None,
        "blocked_non_policy_tool_calls": sum(
            len(item["blocked_tool_calls"]) for item in evaluated
        ),
        "median_latency_seconds": _percentile(latencies, 0.5),
        "p95_latency_seconds": _percentile(latencies, 0.95),
        "token_usage": token_totals,
        "cases": evaluated,
    }


def _parse_ints(value: str) -> tuple[int, ...]:
    try:
        values = tuple(sorted({int(item.strip()) for item in value.split(",")}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Provide positive comma-separated integers."
        ) from exc
    if not values or values[0] < 1:
        raise argparse.ArgumentTypeError("Provide positive comma-separated integers.")
    return values


def _parse_floats(value: str) -> tuple[float, ...]:
    try:
        values = tuple(sorted({float(item.strip()) for item in value.split(",")}))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Provide comma-separated numeric thresholds.") from exc
    if not values or any(not 0 <= item <= 1 for item in values):
        raise argparse.ArgumentTypeError("Similarity thresholds must be between 0 and 1.")
    return values


def run_evaluation(
    cases: list[dict[str, Any]],
    thresholds: tuple[float, ...],
    k_values: tuple[int, ...],
    include_model: bool,
) -> dict[str, Any]:
    collection = get_policy_collection()
    if collection.count() == 0:
        raise RuntimeError(
            "The policy Chroma collection is empty. Run "
            r".\.venv\Scripts\python.exe -m scripts.ingest_bank_policies_chroma data\ first."
        )
    corpus_titles = {
        metadata.get("title")
        for metadata in collection.get(include=["metadatas"])["metadatas"]
        if metadata and metadata.get("title")
    }
    missing_titles = sorted(
        {title for case in cases for title in case["expected_titles"]} - corpus_titles
    )
    if missing_titles:
        raise ValueError(
            "Expected policy title(s) are missing from the Chroma index: "
            + ", ".join(missing_titles)
            + ". Rebuild the index or correct the evaluation labels."
        )
    embeddings = get_embeddings()
    max_k = max(k_values)

    # Warm up model loading and query encoding so reported latency reflects searches, not startup.
    search_policy_collection(
        "bank policy evaluation warm-up",
        collection,
        embeddings,
        top_k=1,
        threshold=0.0,
    )
    threshold_reports = []
    retrieval_for_model: dict[str, dict[str, Any]] = {}
    for threshold in thresholds:
        results = []
        for case in cases:
            started = time.perf_counter()
            search_result = search_policy_collection(
                case["question"],
                collection,
                embeddings,
                category=case["category"],
                top_k=max_k,
                threshold=threshold,
            )
            elapsed = time.perf_counter() - started
            documents = search_result.get("documents", [])
            result = {
                "id": case["id"],
                "found": bool(search_result.get("found")),
                "latency_seconds": round(elapsed, 6),
                "documents": [
                    {
                        "title": document["title"],
                        "source": document["source"],
                        "category": document["category"],
                        "similarity_score": document["similarity_score"],
                    }
                    for document in documents
                ],
            }
            results.append(result)
        metrics = score_retrieval(cases, results, k_values)
        result_by_id = {result["id"]: result for result in results}
        case_metrics = []
        for case in cases:
            result = result_by_id[case["id"]]
            rank = next(
                (
                    index
                    for index, document in enumerate(result["documents"], start=1)
                    if document["title"] in case["expected_titles"]
                ),
                None,
            )
            case_metrics.append(
                {
                    "id": case["id"],
                    "answerable": case["answerable"],
                    "found": result["found"],
                    "expected_document_rank": rank,
                    "top_documents": [
                        {
                            "title": document["title"],
                            "similarity_score": document["similarity_score"],
                        }
                        for document in result["documents"]
                    ],
                }
            )
        threshold_reports.append(
            {"threshold": threshold, "metrics": metrics, "cases": case_metrics}
        )
        if threshold == settings.similarity_threshold:
            retrieval_for_model = {result["id"]: result for result in results}

    report: dict[str, Any] = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "dataset": str(DEFAULT_DATASET.relative_to(PROJECT_ROOT))
        if cases == load_cases(DEFAULT_DATASET)
        else "custom",
        "index": COLLECTION_NAME,
        "index_chunk_count": collection.count(),
        "embedding_model": settings.embedding_model,
        "configured_threshold": settings.similarity_threshold,
        "k_values": list(k_values),
        "retrieval_by_threshold": threshold_reports,
    }
    if include_model:
        if not retrieval_for_model:
            raise ValueError(
                "Include the configured similarity threshold in --thresholds to score answer citations."
            )
        report["model_evaluation"] = evaluate_model(cases, retrieval_for_model)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--thresholds", type=_parse_floats, default=DEFAULT_THRESHOLDS)
    parser.add_argument("--k", type=_parse_ints, default=DEFAULT_K_VALUES)
    parser.add_argument(
        "--include-model",
        action="store_true",
        help="Call the configured LLM; only bank-policy search tool calls are executed.",
    )
    parser.add_argument("--output", type=Path, help="Optional path for a JSON report.")
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.dataset)
        report = run_evaluation(cases, args.thresholds, args.k, args.include_model)
    except (OSError, ValueError, RuntimeError) as exc:
        logging.error("Evaluation failed: %s", exc)
        return 1

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"Report written to {output_path}")
    print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
