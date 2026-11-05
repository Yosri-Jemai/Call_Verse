from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
CLIENT_SIMULATOR_DIR = ROOT / "Client_Simulator"
QUALITY_ANALYST_DIR = ROOT / "Quality_Analyst"
load_dotenv(CLIENT_SIMULATOR_DIR / ".env", override=False)
sys.path.insert(0, str(CLIENT_SIMULATOR_DIR))

from client_simulator import ClientSimulatorAgent, LANGUAGES, SCENARIOS  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a simulated Client Simulator customer against Customer Advisor."
    )
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="normal")
    parser.add_argument(
        "--mood",
        type=float,
        default=0.0,
        help="Starting mood from -1 (very upset) to 1 (very satisfied). Default: 0.",
    )
    parser.add_argument(
        "--language",
        choices=("auto", *LANGUAGES),
        default="auto",
        help="Customer language. Default: selected by the simulator.",
    )
    parser.add_argument("--difficulty", type=float, default=0.0)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument(
        "--client-llm",
        choices=("rules", "auto", "claude", "gemini", "groq", "ollama"),
        default="auto",
        help="Client response engine. 'auto' uses configured credentials, otherwise rules.",
    )
    parser.add_argument("--client-model", help="Optional model name for the client LLM.")
    parser.add_argument(
        "--advisor-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the running Customer Advisor API.",
    )
    parser.add_argument(
        "--customer-id",
        type=int,
        default=1,
        help="Customer Advisor database customer ID to use.",
    )
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--quality-analysis",
        action="store_true",
        help="Evaluate the completed call with Quality Analyst using Groq.",
    )
    parser.add_argument("--quality-model", help="Groq model used by Quality Analyst.")
    parser.add_argument("--quality-report", type=Path, help="Optional path to save the QA report JSON.")
    parser.add_argument(
        "--quality-db",
        default=os.getenv("QA_DATABASE_URL") or str(
            QUALITY_ANALYST_DIR / "data" / "quality_reports.sqlite3"
        ),
        help="PostgreSQL URL or SQLite database path for Quality Analyst.",
    )
    args = parser.parse_args()

    if not -1 <= args.mood <= 1:
        parser.error("--mood must be between -1 and 1.")
    if not 0 <= args.difficulty <= 1:
        parser.error("--difficulty must be between 0 and 1.")
    if args.max_turns < 1:
        parser.error("--max-turns must be at least 1.")
    if args.customer_id < 1:
        parser.error("--customer-id must be at least 1.")
    if args.timeout <= 0:
        parser.error("--timeout must be greater than 0.")
    return args


def make_client_llm(name: str, model: str | None):
    if name == "rules":
        return None

    import llm_backends

    try:
        if name == "auto":
            return llm_backends.auto_llm("client")
        return llm_backends.make_backend(name, "client", model)
    except llm_backends.LLMError:
        if name != "auto":
            raise
        print("No client LLM is configured; using the simulator's rules engine.")
        return None


def request_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Customer Advisor returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(
            f"Cannot reach Customer Advisor at {url}: {exc.reason}. "
            "Start its API and check --advisor-url."
        ) from exc
    except TimeoutError as exc:
        raise RuntimeError(f"Customer Advisor request timed out after {timeout:g} seconds.") from exc

    if not isinstance(result, dict):
        raise RuntimeError("Customer Advisor returned an unexpected response.")
    return result


def get_advisor_response(
    advisor_url: str,
    message: str,
    thread_id: str | None,
    customer_id: int,
    timeout: float,
) -> tuple[dict[str, Any], str | None]:
    endpoint = f"{advisor_url.rstrip('/')}/api/chat"
    payload: dict[str, Any] = {"message": message, "customer_id": customer_id}
    if thread_id:
        payload["thread_id"] = thread_id
    result = request_json(endpoint, payload, timeout)
    current_thread_id = result.get("thread_id", thread_id)

    while result.get("pending_approval"):
        confirmation = result.get("confirmation") or {}
        print("\nCustomer Advisor requested a financial action:")
        print(confirmation.get("message") or result.get("message") or "Review the pending action.")
        details = confirmation.get("details")
        if details:
            print(json.dumps(details, ensure_ascii=False, indent=2, default=str))
        print("Simulation mode: action automatically declined.")
        if not current_thread_id:
            raise RuntimeError("The pending approval response did not include a conversation ID.")
        result = request_json(
            endpoint,
            {"thread_id": current_thread_id, "approved": False, "customer_id": customer_id},
            timeout,
        )

    return result, current_thread_id


def run(args: argparse.Namespace) -> int:
    client_llm = make_client_llm(args.client_llm, args.client_model)
    language_weights = None if args.language == "auto" else {args.language: 1.0}
    simulator = ClientSimulatorAgent(
        seed=args.seed,
        llm=client_llm,
        max_turns=args.max_turns,
        difficulty=args.difficulty,
        language_weights=language_weights,
    )

    requests = simulator.generate_requests(
        scenario=args.scenario,
        duration_min=10,
        difficulty=args.difficulty,
    )
    if not requests:
        raise RuntimeError("No customer was generated. Try another seed or scenario.")
    client_request = simulator.rng.choice(requests)
    client_request.initial_mood = args.mood
    state = simulator.start(client_request)

    print(f"Scenario: {args.scenario}")
    print(f"Starting mood: {args.mood:+.2f}; language: {client_request.language}")
    print(f"Client engine: {simulator.llm_backend}; advisor: {args.advisor_url}")
    print("\nConversation")
    client_message = client_request.opening_message
    print(f"Customer: {client_message}")

    thread_id = None
    for _ in range(args.max_turns):
        advisor_result, thread_id = get_advisor_response(
            args.advisor_url,
            client_message,
            thread_id,
            args.customer_id,
            args.timeout,
        )
        advisor_message = advisor_result.get("message")
        if not isinstance(advisor_message, str) or not advisor_message.strip():
            raise RuntimeError("Customer Advisor returned no message.")
        print(f"Advisor: {advisor_message}")
        client_reply = simulator.reply(state, advisor_message.strip())
        if client_reply.strip():
            print(f"Customer: {client_reply}")
        if state.status != "active":
            break
        if not client_reply.strip():
            raise RuntimeError("The client simulator returned an empty message.")
        client_message = client_reply

    if state.status == "active":
        state.status = "max_turns"

    summary = simulator.summary(state, include_qa=not args.quality_analysis)
    print("\nSimulation result")
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    if args.quality_analysis:
        sys.path.insert(0, str(QUALITY_ANALYST_DIR))
        try:
            from analyst import QualityAnalystError, analyze_case, case_from_simulation
            from storage import QualityStorageError, save_conversation, save_report

            case = case_from_simulation(state, args.scenario)
            save_conversation(case, args.quality_db)
            report = analyze_case(case, model=args.quality_model)
            report["database_id"] = save_report(report, args.quality_db, case=case)
            rendered_report = json.dumps(report, ensure_ascii=False, indent=2, default=str)
            if args.quality_report:
                args.quality_report.parent.mkdir(parents=True, exist_ok=True)
                args.quality_report.write_text(rendered_report + "\n", encoding="utf-8")
            print("\nQuality Analyst report")
            print(rendered_report)
        except (QualityAnalystError, QualityStorageError, OSError) as exc:
            raise RuntimeError(f"Quality Analyst could not evaluate the call: {exc}") from exc
    return 0


def main() -> int:
    args = parse_args()
    try:
        return run(args)
    except (RuntimeError, ValueError, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            print("\nSimulation interrupted.", file=sys.stderr)
            return 130
        print(f"Simulation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
