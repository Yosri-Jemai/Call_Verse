# Evaluation architecture

The evaluation tools are separate from the Customer Advisor API. You start
them manually; they do not process live conversations.

## Deterministic retrieval benchmark

```text
policy_qa.jsonl
      |
      v
evaluate_policy_quality.py (.venv)
  - queries local Chroma policy index
  - calculates Recall@k, MRR, rejection, and latency
      |
      +--> terminal output
      `--> optional JSON report
```

Default run:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_policy_quality
```

It does not call an LLM. Add `--thresholds`, `--k`, `--dataset`, or `--output`
to adjust the benchmark, choose another labeled set, or save results. See
[README.md](./README.md) for examples and option meanings.

## Configured-model answer evaluation

Adding `--include-model` to the deterministic benchmark also sends each
synthetic question to the configured model using the advisor's prompt. The
evaluation executes policy search only; all other tools are blocked. It checks
required facts, relevant-source citations, and abstention on unsupported
questions. Provider calls may incur cost. This mode does not start the API or
perform database/approval operations.

## RAGChecker retrieval pilot

```text
Four reviewed questions + reference answers
                    |
                    v
evaluate_ragchecker.py (.venv)
  - retrieves passages from local Chroma
  - computes deterministic retrieval checks
                    |
                    v
ragchecker_worker.py (.venv-ragchecker)
  - runs RAGChecker retriever metrics
  - calls Groq through LiteLLM
                    |
                    v
reports/ragchecker-pilot.json
```

Run it separately with:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_ragchecker
```

The worker is a short-lived subprocess. The advisor API is not started or
called. The pilot sends its synthetic questions, reference answers, and
retrieved policy passages to Groq; it does not send customer conversation data.
It evaluates retrieval only—not generated answers, faithfulness, hallucination,
or every possible customer scenario.

## Data and reports

- `policy_qa.jsonl` — reviewed synthetic questions, expected policy titles,
  required facts, topic, and language.
- `ragchecker_pilot.jsonl` — the smaller set of RAGChecker cases and reference
  answers.
- `reports/policy-rag-baseline.json` — deterministic retrieval report.
- `reports/ragchecker-pilot.json` — latest RAGChecker pilot report.

The test documents are synthetic and not exhaustive. Review labels against
source policies when the corpus changes. The Romanized Tunisian Arabic examples
need fluent-speaker review before being used to assess language quality.
