"""Run a small RAGChecker pilot on reviewed, synthetic policy questions."""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from src.agent.policy_rag import TOP_K, get_embeddings, get_policy_collection, search_policy_collection
from src.config import settings

from evaluation.evaluate_policy_quality import (
    DEFAULT_DATASET,
    PROJECT_ROOT,
    load_cases,
    score_retrieval,
)

DEFAULT_PILOT = Path(__file__).with_name("ragchecker_pilot.jsonl")
DEFAULT_REPORT = Path(__file__).with_name("reports") / "ragchecker-pilot.json"
DEFAULT_WORKER_PYTHON = PROJECT_ROOT / ".venv-ragchecker" / "Scripts" / "python.exe"
logger = logging.getLogger("cashy.evaluation.ragchecker")


def load_pilot(path: Path, dataset_path: Path) -> list[dict[str, Any]]:
    cases_by_id = {case["id"]: case for case in load_cases(dataset_path)}
    pilot = []
    seen_ids = set()
    with path.open(encoding="utf-8") as file:
        for line_number, line in enumerate(file, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: invalid JSON.") from exc
            if not isinstance(item, dict):
                raise ValueError(f"{path}:{line_number}: each pilot row must be an object.")
            if set(item) != {"id", "gt_answer"}:
                raise ValueError(f"{path}:{line_number}: expected only id and gt_answer.")
            case_id = item["id"]
            if not isinstance(case_id, str) or case_id not in cases_by_id:
                raise ValueError(f"{path}:{line_number}: unknown case id {case_id!r}.")
            if case_id in seen_ids:
                raise ValueError(f"{path}:{line_number}: duplicate case id {case_id!r}.")
            if not cases_by_id[case_id]["answerable"]:
                raise ValueError(f"{path}:{line_number}: RAGChecker pilot cases must be answerable.")
            if not isinstance(item["gt_answer"], str) or not item["gt_answer"].strip():
                raise ValueError(f"{path}:{line_number}: gt_answer must be non-empty.")
            pilot.append({**cases_by_id[case_id], "gt_answer": item["gt_answer"].strip()})
            seen_ids.add(case_id)
    if not pilot:
        raise ValueError(f"No RAGChecker pilot cases found in {path}.")
    return pilot


def retrieve_pilot_cases(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    collection = get_policy_collection()
    if collection.count() == 0:
        raise RuntimeError(
            "The policy Chroma collection is empty. Ingest policies before running this pilot."
        )
    embeddings = get_embeddings()
    results = []
    for case in cases:
        started = time.perf_counter()
        search_result = search_policy_collection(
            case["question"],
            collection,
            embeddings,
            category=case["category"],
            top_k=TOP_K,
            threshold=settings.similarity_threshold,
        )
        documents = search_result.get("documents", [])
        results.append(
            {
                "id": case["id"],
                "found": bool(search_result.get("found")),
                "latency_seconds": time.perf_counter() - started,
                "documents": documents,
            }
        )
    return results


def build_ragchecker_input(
    cases: list[dict[str, Any]],
    retrieval_results: list[dict[str, Any]],
) -> dict[str, Any]:
    retrieval_by_id = {result["id"]: result for result in retrieval_results}
    if len(retrieval_by_id) != len(retrieval_results):
        raise ValueError("Retrieval results contain duplicate case IDs.")
    if set(retrieval_by_id) != {case["id"] for case in cases}:
        raise ValueError("Retrieval result IDs must match pilot case IDs.")
    return {
        "results": [
            {
                "query_id": case["id"],
                "query": case["question"],
                "gt_answer": case["gt_answer"],
                "response": "",
                "retrieved_context": [
                    {
                        "doc_id": f"{document['source']}::{document['title']}",
                        "text": document["text"],
                    }
                    for document in retrieval_by_id[case["id"]]["documents"]
                ],
            }
            for case in cases
        ]
    }


def _validate_groq_configuration() -> tuple[str, str]:
    if settings.resolved_provider != "groq" or not settings.groq_api_key:
        raise RuntimeError(
            "The RAGChecker pilot currently requires a configured Groq provider and API key."
        )
    model_name = settings.model_name or "openai/gpt-oss-120b"
    model = f"groq/{model_name}"
    return model, settings.groq_api_key


def run_pilot(
    cases: list[dict[str, Any]],
    report_path: Path,
    worker_python: Path,
) -> dict[str, Any]:
    if not worker_python.is_file():
        raise RuntimeError(
            "The isolated RAGChecker environment is missing. Create it using the "
            "evaluation README instructions or pass --worker-python."
        )

    retrieval_results = retrieve_pilot_cases(cases)
    ragchecker_input = build_ragchecker_input(cases, retrieval_results)
    input_results = score_retrieval(cases, retrieval_results, (1, 2, TOP_K))
    model, groq_api_key = _validate_groq_configuration()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="cashy-ragchecker-") as temp_dir:
        temp_path = Path(temp_dir)
        input_path = temp_path / "input.json"
        worker_output_path = temp_path / "worker-output.json"
        input_path.write_text(
            json.dumps(ragchecker_input, ensure_ascii=False),
            encoding="utf-8",
        )
        worker_env = os.environ.copy()
        worker_env["GROQ_API_KEY"] = groq_api_key
        worker_env["RAGCHECKER_MODEL"] = model
        advisor_site_packages = PROJECT_ROOT / ".venv" / "Lib" / "site-packages"
        worker_env["PYTHONPATH"] = os.pathsep.join(
            filter(
                None,
                (
                    str(advisor_site_packages),
                    worker_env.get("PYTHONPATH"),
                ),
            )
        )
        completed = subprocess.run(
            [
                str(worker_python),
                str(Path(__file__).with_name("ragchecker_worker.py")),
                "--input",
                str(input_path),
                "--output",
                str(worker_output_path),
            ],
            env=worker_env,
            check=False,
            timeout=270,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"The isolated RAGChecker worker exited with status {completed.returncode}."
            )
        worker_result = json.loads(worker_output_path.read_text(encoding="utf-8"))

    retrieval_by_id = {result["id"]: result for result in retrieval_results}
    report = {
        "pilot_size": len(cases),
        "index": "bank_policies",
        "configured_threshold": settings.similarity_threshold,
        "evaluator_model": model,
        "metrics_scope": "retriever_metrics only",
        "deterministic_recall_metrics": input_results,
        "ragchecker_metrics": worker_result["metrics"],
        "ragchecker_case_metrics": worker_result["cases"],
        "deterministic_fact_coverage": {
            case["id"]: all(
                any(
                    alternative.casefold() in " ".join(
                        document["text"] for document in retrieval_by_id[case["id"]]["documents"]
                    ).casefold()
                    for alternative in alternatives
                )
                for alternatives in case["required_fact_groups"]
            )
            for case in cases
        },
        "limitations": [
            "This pilot measures retrieval against reviewed reference-answer claims only.",
            "It does not evaluate generated advisor answers, faithfulness, or hallucination.",
            "The sample is small and the RAGChecker claim extraction/checking is model-judged.",
        ],
        "cases": [
            {
                "id": case["id"],
                "question": case["question"],
                "required_fact_groups": case["required_fact_groups"],
                "retrieved_titles": [
                    document["title"]
                    for document in retrieval_by_id[case["id"]]["documents"]
                ],
            }
            for case in cases
        ],
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--pilot", type=Path, default=DEFAULT_PILOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--worker-python", type=Path, default=DEFAULT_WORKER_PYTHON)
    args = parser.parse_args(argv)
    try:
        cases = load_pilot(args.pilot, args.dataset)
        report_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
        worker_python = args.worker_python.resolve()
        report = run_pilot(cases, report_path, worker_python)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        logger.error("RAGChecker pilot failed: %s", exc)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
