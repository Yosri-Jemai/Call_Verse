# Evaluation architecture

The evaluation tools run separately from the Customer Advisor. They are
started manually and do not process live conversations.

## RAGChecker flow

```text
Reviewed pilot questions + reference answers
                    |
                    v
evaluate_ragchecker.py (Customer Advisor .venv)
  - retrieves passages from the local Chroma policy index
  - calculates deterministic retrieval metrics
  - passes questions, answers, and passages to a worker
                    |
                    v
ragchecker_worker.py (.venv-ragchecker)
  - runs RAGChecker claim/retrieval metrics
  - calls the configured Groq model through LiteLLM
                    |
                    v
reports/ragchecker-pilot.json
```

The worker is a short-lived subprocess. The advisor API and agent are not
started or called. The pilot sends only its synthetic questions, reference
answers, and retrieved policy passages to Groq; it does not send customer
conversation data.

## Run the pilot

Open PowerShell in the `Customer_Advisor` directory and run:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_ragchecker
```

No virtual-environment activation is needed. The command uses the advisor
environment to retrieve from Chroma, then launches
`.venv-ragchecker\Scripts\python.exe` for RAGChecker.

Before the first run, the advisor environment, local policy index, Groq
configuration, and isolated `.venv-ragchecker` environment must be available.
See [README.md](./README.md) for setup instructions.

## Scope

The checked-in pilot currently contains four reviewed, answerable policy
questions. It evaluates retrieval only—not generated advisor answers,
faithfulness, hallucination, or every possible customer scenario. The JSON
report includes both deterministic retrieval results and RAGChecker metrics.

## Related files

- `evaluate_ragchecker.py` — retrieval, orchestration, and report creation.
- `ragchecker_worker.py` — isolated RAGChecker and Groq evaluation process.
- `ragchecker_pilot.jsonl` — pilot questions and reference answers.
- `reports/ragchecker-pilot.json` — most recently saved pilot results.
- `evaluate_policy_quality.py` — broader deterministic policy benchmark.
