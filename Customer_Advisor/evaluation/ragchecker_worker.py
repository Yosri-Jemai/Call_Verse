"""Isolated worker for RAGChecker to avoid its legacy dependency conflicts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _exclude_global_site_packages() -> None:
    global_site_packages = Path(sys.base_prefix) / "Lib" / "site-packages"
    sys.path[:] = [
        path
        for path in sys.path
        if Path(path).resolve() != global_site_packages.resolve()
    ]


def _groq_llm_callback(prompts: list[str]) -> list[str]:
    import litellm

    model = os.environ["RAGCHECKER_MODEL"]
    responses = []
    for prompt in prompts:
        response = litellm.completion(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            timeout=30,
            num_retries=5,
            retry_strategy="exponential_backoff_retry",
            temperature=0,
        )
        content = response.choices[0].message.content
        if not isinstance(content, str) or not content:
            raise RuntimeError("The configured evaluator model returned an empty response.")
        responses.append(content)
    return responses


def run_worker(input_path: Path, output_path: Path) -> dict[str, Any]:
    _exclude_global_site_packages()
    from ragchecker import RAGChecker, RAGResults
    from ragchecker.metrics import retriever_metrics

    model = os.environ["RAGCHECKER_MODEL"]
    rag_results = RAGResults.from_json(input_path.read_text(encoding="utf-8"))
    evaluator = RAGChecker(
        extractor_name=model,
        checker_name=model,
        batch_size_extractor=1,
        batch_size_checker=1,
        custom_llm_api_func=_groq_llm_callback,
        joint_check=True,
        joint_check_num=5,
    )
    metrics = evaluator.evaluate(rag_results, metrics=retriever_metrics)
    result = {
        "metrics": metrics,
        "cases": [
            {
                "query_id": item.query_id,
                **item.metrics,
            }
            for item in rag_results.results
        ],
    }
    output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = run_worker(args.input, args.output)
    print(json.dumps(result["metrics"], indent=2))


if __name__ == "__main__":
    main()
