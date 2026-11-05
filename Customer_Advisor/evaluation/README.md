# Customer Advisor evaluation

These are **manual, offline evaluations** of the synthetic policy corpus. They
do not start the advisor API, run alongside customer conversations, or use
customer records. Run commands from the `Customer_Advisor` directory in
PowerShell. You do not need to activate the virtual environment: each command
calls its Python executable directly.

See [ARCHITECTURE.md](./ARCHITECTURE.md) for how the evaluation flows fit
together.

## Before running

Install the project's existing dependencies and make sure the local
`bank_policies` Chroma index and embedding model are available. If the index is
empty or the policy source has changed, ingest the policies first:

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_bank_policies_chroma data\
```

The default labeled dataset is `policy_qa.jsonl` (51 synthetic questions:
41 answerable and 10 unsupported, across 13 topics and English, French, and
Romanized Tunisian Arabic). Check its labels against source policies when those
policies change. Have a fluent Tunisian Arabic speaker review the transliterated
examples before drawing language-quality conclusions.

## 1. Retrieval benchmark (recommended first)

Run the benchmark using its defaults:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_policy_quality
```

This is retrieval-only: it does not call an LLM. It prints metrics to the
terminal and does not save a report unless `--output` is supplied.

To compare several similarity thresholds and top-k cutoffs and save the report:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_policy_quality `
  --thresholds 0.45,0.55,0.65 `
  --k 1,2,4 `
  --output evaluation\reports\policy-rag-baseline.json
```

Options:

- `--thresholds`: comma-separated similarity cutoffs from 0 to 1. A higher
  cutoff is stricter about accepting retrieved passages. Default: `0.45,0.55,0.65`.
- `--k`: comma-separated result counts used for Recall@k. Default: `1,2,4`.
- `--output`: optional JSON report path, relative to `Customer_Advisor` unless
  absolute. Saving to an existing path replaces that report.
- `--dataset`: optional path to another JSONL dataset. Defaults to
  `evaluation\policy_qa.jsonl`.
- `--help`: show all CLI options.

The report includes Recall@k, MRR, answerable retrieval rate, unsupported-query
false-positive/rejection rates, retrieval latency, topic/language counts, and
per-question retrieved titles. The checked-in report is
`reports/policy-rag-baseline.json`.

## 2. Configured-model answer evaluation (optional)

Add `--include-model` to the retrieval benchmark command to ask the configured
advisor model to answer each labeled question:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_policy_quality `
  --thresholds 0.45,0.55,0.65 `
  --k 1,2,4 `
  --include-model `
  --output evaluation\reports\advisor-baseline.json
```

This makes hosted-model calls and may incur provider cost or be affected by
provider rate limits. Only `search_bank_policies` calls are executed; any other
tool call is blocked. It does not run database or approval operations. The
evaluation reports simple required-fact, source-citation, and abstention
checks; these are regression signals, not a substitute for human review.
Tracing may send data to LangSmith if tracing is enabled in the environment.
The evaluation does not print API keys.

To try a smaller set, use `--dataset path\to\small-set.jsonl`. Keep the same
required JSONL fields as `policy_qa.jsonl`; use `--help` to see the CLI options.

## 3. RAGChecker retrieval pilot (optional)

RAGChecker is a separate, manually started diagnostic pilot over four reviewed,
answerable cases. It does not evaluate generated advisor answers,
faithfulness, or hallucination. It sends only those synthetic questions,
reference answers, and retrieved policy passages to Groq through LiteLLM; calls
may incur provider usage and hit rate limits.

Run it with:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_ragchecker
```

It uses the advisor environment for Chroma retrieval, then starts the
short-lived `.venv-ragchecker` worker. Groq must be configured in the project's
environment. The result is saved to `reports/ragchecker-pilot.json`.

If needed, the runner accepts:

- `--pilot`: alternate pilot JSONL file.
- `--dataset`: alternate labeled benchmark file used to validate pilot IDs.
- `--output`: alternate report path.
- `--worker-python`: path to the isolated worker's Python executable.
- `--help`: show CLI options.

### One-time RAGChecker environment setup

RAGChecker is not installed in the advisor environment. Its RefChecker
dependency has an Anthropic version requirement that conflicts with this
project, so keep its packages isolated. The worker reuses the advisor
environment's existing ML packages and filters out global Python packages.

```powershell
.\.venv\Scripts\python.exe -m venv --system-site-packages --clear .venv-ragchecker
.\.venv-ragchecker\Scripts\python.exe -m pip install --no-deps --ignore-installed ragchecker==0.1.9 refchecker==0.2.17 dataclasses-json loguru accelerate boto3 beautifulsoup4 diskcache litellm nltk plotly pytorch-lightning rank-bm25 spacy importlib-metadata
```

Install the spaCy English model only if it is not already available:

```powershell
.\.venv-ragchecker\Scripts\python.exe -m spacy download en_core_web_sm
```

Do not install these optional evaluator packages into the advisor's production
environment.

## How to inspect and refresh results

Use the deterministic retrieval benchmark first to identify misses and
unsupported-query false positives. Review per-case details in the JSON report.
Use the configured-model mode to inspect answer behavior, and use RAGChecker as
a separate retrieval diagnostic. RAGChecker's metrics are LLM-judged; the
four-case pilot is too small to establish production thresholds.

To see all options for either runner:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_policy_quality --help
.\.venv\Scripts\python.exe -m evaluation.evaluate_ragchecker --help
```
