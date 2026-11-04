# Customer Advisor quality evaluation

This package is intentionally separate from the customer-facing agent and its
runtime endpoints. It evaluates the local Chroma policy index and optionally
runs a safe, policy-only model benchmark. It uses the checked-in synthetic
policies and question labels; no customer records are needed.

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
