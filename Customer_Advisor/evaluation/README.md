# Customer Advisor quality evaluation

This package is intentionally separate from the customer-facing agent and its
runtime endpoints. It evaluates the local Chroma policy index and optionally
runs a safe, policy-only model benchmark. It uses the checked-in synthetic
policies and question labels; no customer records are needed.

## Quick start

From the `Customer_Advisor` directory, run the RAGChecker pilot with:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_ragchecker
```

You do not need to activate the virtual environment first. The command uses
`.venv` for retrieval and starts the separate `.venv-ragchecker` worker. It does
not start the advisor or run alongside live conversations. See
[ARCHITECTURE.md](./ARCHITECTURE.md) for the flow and scope.

## Requirements

Use the Customer Advisor virtual environment with its existing `.[rag]` and
development dependencies installed. No additional evaluation package is needed.
The benchmark needs the `bank_policies` Chroma index to be ingested and the local
embedding model available.

From `Customer_Advisor`:

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_bank_policies_chroma data\
.\.venv\Scripts\python.exe -m evaluation.evaluate_policy_quality
```

By default, the script runs retrieval-only. It reports `Recall@k`, MRR,
answerable retrieval rate, false-positive/rejection rates for unanswerable
questions, and median/p95 retrieval latency while sweeping similarity thresholds.
It bypasses Redis to measure the underlying Chroma + embedding retrieval path.
Tune the threshold only after reviewing both answerable misses and unsupported
question false positives; the checked-in set is an initial benchmark, not a
production acceptance standard.

## Optional RAGChecker pilot

RAGChecker is an optional dependency, not part of the advisor runtime. Its
current `refchecker` dependency conflicts with this project's
`langchain-anthropic` version range. Use a separate environment that reuses the
advisor environment's already-installed ML stack, then install RAGChecker and
its non-Anthropic evaluator dependencies:

```powershell
.\.venv\Scripts\python.exe -m venv --system-site-packages --clear .venv-ragchecker
.\.venv-ragchecker\Scripts\python.exe -m pip install --no-deps --ignore-installed ragchecker==0.1.9 refchecker==0.2.17 dataclasses-json loguru accelerate boto3 beautifulsoup4 diskcache litellm nltk plotly pytorch-lightning rank-bm25 spacy importlib-metadata
```

The evaluation uses Groq via LiteLLM, so it does not install or use
RefChecker's incompatible Anthropic SDK requirement. The isolated worker imports
the existing PyTorch/Transformers packages from the advisor environment without
installing or upgrading them, and excludes global packages that could shadow
them. Do not install the optional RAGChecker packages into the advisor's
production virtual environment. `--no-deps --ignore-installed` keeps this
manual dependency set isolated rather than resolving or replacing packages
visible through system site-packages. Install the spaCy English model only if
it is not already available from the advisor environment:

```powershell
.\.venv-ragchecker\Scripts\python.exe -m spacy download en_core_web_sm
```

The four reviewed cases in `ragchecker_pilot.jsonl` compare RAGChecker's
claim-level retrieval metrics with the existing deterministic title Recall@k
and required-fact coverage checks. This first pilot deliberately runs
`retriever_metrics` only; it does not score advisor-generated answers,
faithfulness, or hallucination. The RAGChecker extractor/checker use the
configured Groq model. Calls are serial, allow up to five attempts with
exponential backoff for transient provider errors, and use a 30-second
per-call timeout. They send only synthetic questions, reference answers, and
retrieved policy passages. They may incur provider usage. The saved results go
to `reports/ragchecker-pilot.json`.

Run it from `Customer_Advisor`:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_ragchecker
```

The runner uses `.venv-ragchecker\Scripts\python.exe` by default. If you placed
the isolated interpreter elsewhere, pass its path with `--worker-python`.

RAGChecker's claim extraction and checking are themselves LLM judgments. Treat
this small pilot as a diagnostic comparison, not a quality threshold or a
replacement for manually reviewed labels and deterministic tests. Consider
expanding it only if its per-case diagnoses add useful information.

The retrieval snapshot from the current local index is saved in
`reports/policy-rag-baseline.json`; rerun the `--output` command below after
changing the corpus, labels, or retriever to refresh it.

Use a persistent JSON report when comparing changes:

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_policy_quality `
  --thresholds 0.45,0.55,0.65 `
  --k 1,2,4 `
  --output evaluation\reports\policy-rag-baseline.json
```

The labeled cases live in `policy_qa.jsonl`. Each answerable case names the
expected document title(s); unanswerable cases have an empty title list. Extend
the set with independently reviewed paraphrases, languages, edge cases, and
unsupported questions whenever the source policies change.

## Optional configured-model evaluation

Add `--include-model` to run the same synthetic questions through the configured
provider and the production advisor system prompt. The model may execute only
`search_bank_policies`; any other requested tool is blocked and counted. This
mode reports required-fact checks, relevant-source citation rate, abstention
rate on unanswerable questions, blocked tool attempts, latency, and token usage
when the provider returns it. These deterministic checks are useful regression
signals, not a substitute for human review or a semantic LLM judge.

The evaluator intercepts policy-search tool calls and executes the same Chroma
retrieval function directly. It does not execute database/approval tools, call
the production tool wrapper, or write evaluation queries to Redis.

```powershell
.\.venv\Scripts\python.exe -m evaluation.evaluate_policy_quality `
  --thresholds 0.45,0.55,0.65 `
  --include-model `
  --output evaluation\reports\advisor-baseline.json
```

Model mode makes hosted-provider calls using the configured credentials and may
incur provider cost. It uses only this synthetic policy dataset, never customer
records. The agent's LangSmith tracing configuration may also send traces to
LangSmith; disable tracing in the running process if that is not desired. The
evaluation does not read or print API keys.

## Metric caveats

- Recall@k and MRR use the manually labeled relevant titles. Review labels
  whenever policies are added, renamed, or duplicated.
- The default corpus contains synthetic policy documents. High scores only show
  performance on this corpus and dataset, not real-bank policy accuracy.
- Retrieval latency includes local query embedding and Chroma search, after one
  warm-up query; it excludes API/model response latency.
- Model fact checks use reviewed keyword alternatives and citation substring
  matching. They can flag paraphrases or miss subtle contradictions; inspect
  failed case details and manually review sampled passing answers.
