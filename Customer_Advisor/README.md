# Cashy Agent Service

Cashy is a Python banking-support agent backend intended to be called by a separate Next.js application. It uses LangGraph and PostgreSQL tools, provides an HTTP API, and includes a tiny standalone browser chat page for local testing.

> **Prototype safety:** The API is for local testing with demo data. It has no caller authentication or customer-level authorization. Do not expose it to the internet or load real customer data until identity verification, authorization, audit logging, and sensitive-data controls are implemented. Never ingest passwords, PINs, CVVs, full card numbers, or one-time codes.

## Installation après un clone

Prérequis : Python 3.10+ et un serveur PostgreSQL. Exécute ces commandes dans
PowerShell depuis le dossier `Customer_Advisor`.

```powershell
# 1. Créer l'environnement virtuel
py -3 -m venv .venv

# 2. Installer toutes les dépendances épinglées (application, RAG et outils dev)
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Alternative avec uv
uv sync --extra rag --extra dev

# 3. Créer la configuration locale
Copy-Item .env.example .env
```

Renseigne dans `.env` la clé du fournisseur choisi (`LLM_PROVIDER` et la clé
correspondante), ainsi que les paramètres de connexion PostgreSQL. L'application
utilise toujours la base `cashy_banking`, quel que soit le mode. Ne mets jamais de vraie clé
dans `.env.example` ni dans Git. Pour Groq, configure `LLM_PROVIDER=groq` et
`GROQ_API_KEY`; `MODEL_NAME` peut remplacer le modèle Groq par défaut.

```powershell
# 4. Créer la base demo et charger le schéma/les données d'exemple
createdb -U postgres cashy_banking
psql -U postgres -d cashy_banking -f scripts\seed_demo_db.sql

# 5. Importer les politiques bancaires dans l'index Chroma local
.\.venv\Scripts\python.exe -m scripts.ingest_bank_policies_chroma data\

# 6. Lancer l'API
.\.venv\Scripts\python.exe app.py
```

Le premier import ou la première recherche télécharge localement le modèle
`intfloat/multilingual-e5-small` (environ 470 Mo); une connexion Internet est requise
pour ce téléchargement. Chroma est ensuite persisté sous `data/chroma/`, qui peut
être reconstruit en réimportant les fichiers JSONL.

`requirements.txt` est la liste épinglée à utiliser après un clone.
`requirements-venv.txt` est un instantané local ancien et incomplet; il n'est pas
maintenu. Ne l'utilise pas pour installer l'application.

## Run locally on Windows

Requirements: Python 3.10+ and a running PostgreSQL server. The commands below use
PowerShell from the `Customer_Advisor` project directory. Follow the clone-installation
steps above to create the environment and start the API.

This command keeps the server running in the current terminal; press `Ctrl+C` to stop it.
The service binds to `http://127.0.0.1:8000` by default. Open that address for the
standalone chat page. If port 8000 is already in use, choose another port for this PowerShell
session, for example `$env:API_PORT = "8010"`, before running the command, then open
`http://127.0.0.1:8010`. PostgreSQL must permit the configured local authentication method;
set `DB_PASSWORD` if your server requires a password.

The chat page also supports local speech-to-text through the microphone button. On the
first recording, faster-whisper downloads the `small` model (about 500 MB); inference runs
on the CPU and sends the transcript to Cashy automatically when you stop recording.
The browser needs microphone permission; browser media recording works on localhost without
an HTTPS certificate.

Read-aloud uses Microsoft Edge TTS through the `edge-tts` service. The text of assistant
responses is sent to that external service to generate audio; leave read-aloud disabled if
you do not want response text (which may include demo balances or transactions) sent there.
French, English, and Arabic voices are supported, with word timing metadata returned by the
TTS API.

## Bank-policy RAG

Bank-policy retrieval uses a persistent local ChromaDB collection named
`bank_policies` in `data/chroma/` with cosine distance. Sentence Transformers runs the
multilingual `intfloat/multilingual-e5-small` embedding model locally. Documents use
the `passage: ` prefix and questions use `query: `. The model is preloaded during API
startup and remains cached once per process.
No policy text is sent to a hosted embedding API.

The importer accepts one or more JSONL files or folders (searched recursively). The
four existing files in `data/` use UTF-8 JSONL and the schema below:

```json
{"category":"fees","title":"Official document title","content":"Paste the exact approved policy text here.","source":"Official tariff guide, section 2","source_url":"https://example.invalid/tariffs","valid_from":"2026-01-01","valid_until":null}
```

Required fields are `category`, `title`, `content`, and `source`; `source_url`,
`valid_from`, and `valid_until` are optional. Dates must use `YYYY-MM-DD`. Do not
import invented policy, customer records, passwords, PINs, CVVs, full card numbers,
or one-time codes. Imported text is untrusted reference data, never agent
instructions. Invalid JSONL lines and documents that contain credential/card-like
data are reported and skipped without stopping the other files.

From the project directory, import the four files:

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_bank_policies_chroma data\
```

The importer prints document and chunk counts. Chunk IDs are SHA-256 hashes of
`source`, `title`, and `chunk_index`; importing again upserts the same records instead
of adding duplicates. Pass one or more paths to limit the import, and add `--reset`
to clear the collection before importing:

```powershell
.\.venv\Scripts\python.exe -m scripts.ingest_bank_policies_chroma data\bank_policies_fees.jsonl data\bank_policies_limits.jsonl --reset
```

`EMBEDDING_MODEL` selects the Sentence Transformers model. `SIMILARITY_THRESHOLD`
sets the minimum cosine similarity (default `0.55`, range `0` to `1`); search returns
up to four passages and reports no relevant document if every score is below the
threshold. Tune this value against representative questions and unrelated queries.
The agent must answer policy questions only from returned passages, cite `source`
for every fee, limit, or procedure, and say when the passages do not support an
answer. The old PostgreSQL full-text policy table and importer are retained as
legacy code; they are no longer used for policy retrieval.

### Optional Redis retrieval cache

Redis caches only the results of bank-policy searches. Chroma remains the source
of truth, and customer balances, transactions, profiles, and LLM responses are
never placed in this cache. Cache keys are hashed and scoped by the normalized
question, category, current date, embedding model, and retrieval settings. Entries
have no expiration and are shared by all advisor instances connected to this Redis
database. The Redis container is configured as an in-memory cache: stopping or
restarting it clears the entries. To avoid silently evicting old entries, Redis
uses `noeviction` with a 128 MB limit; once full, new cache writes fail and Chroma
continues to serve uncached searches.

Start Docker Desktop, then from the repository root run:

```powershell
docker compose -f .\Customer_Advisor\docker-compose.redis.yml up -d
```

The project launcher also starts this local Redis container when Docker is
available. To check it, run:

```powershell
docker compose -f .\Customer_Advisor\docker-compose.redis.yml ps
```

Configure `REDIS_URL` and `REDIS_CACHE_ENABLED` in `.env` to change the connection
or disable the cache. Redis is an optional performance layer: if it is
disabled or unavailable, the agent logs the cache failure and searches Chroma
normally. No Redis password is configured because the container binds only to
the local machine; do not expose that port publicly.

## HTTP endpoints

- `GET /health` — readiness status
- `POST /api/chat` — send a message and optionally continue a thread
- `GET /docs` — interactive OpenAPI docs for local testing
- `GET /` — minimal standalone chat page, served by the local API

Example request:

```json
{
  "message": "What accounts do I have?",
  "thread_id": null
}
```

The response includes a `thread_id`; send it with later messages to continue the same conversation. Financial transaction changes and bank-account blocks return `pending_approval: true` and a `confirmation` object before changing data. The dashboard displays the advisor's pending response in the transcript before pausing and queues a separate admin summary. The admin inbox stores the request and resumes the same thread after a decision, including a snapshot of the customer's ID, name, and email loaded from PostgreSQL. A name/email profile update is a limited direct change; account blocks set `accounts.is_active` to `false` only after admin approval. Unsupported services such as card blocks, transaction disputes, actual transfers, limit changes, and other profile changes can be sent for manual follow-up. Approving a manual-service request does not execute the bank operation or change customer data; it only authorizes an administrator to handle it outside this prototype. Requests to bypass identity/security controls, use credentials/one-time codes, or perform unauthorized actions can be saved as review-only entries; neither the advisor nor an admin approval can execute those requests. To resume an interrupted write, send the same thread ID and an `approved` boolean:

```json
{
  "thread_id": "the-thread-id-from-the-previous-response",
  "approved": true
}
```

The standalone test page calls the API on the same origin. The API also accepts browser requests from `http://localhost:3000` and `http://127.0.0.1:3000` for a local Next.js frontend. The API binds to loopback by default. Before deployment, add authentication, authorization, and an explicit CORS policy appropriate for the hosting environment.

## Architecture

```text
Browser (web/)
    │ GET /             ┌──────────────────────────┐
    └──────────────────►│ FastAPI (src/api.py)     │
    │ POST /api/chat    │ serves UI + chat API     │
    └──────────────────►└────────────┬─────────────┘
                                     │ thread-scoped invoke/resume
                                     ▼
                         LangGraph (src/agent/graph.py)
                           ├── LLM node (Gemini, etc.)
                           │     └── SQLite style-example search
                           └── Tool node (src/tools/)
                                  └── PostgreSQL (customer/demo facts)

                         PostgreSQL checkpointer
                         (conversation history by thread ID)
```

The browser page is a small client served by FastAPI from the same origin and port. The API passes each message to the compiled LangGraph agent. The agent can answer conversationally or call registered tools; tool calls query PostgreSQL. LangGraph checkpoints conversation state by `thread_id`. Write tools interrupt the graph and return a pending confirmation to the client; the client must explicitly approve or reject before the graph resumes.

## File guide

### Application and configuration

| File | Responsibility |
|---|---|
| `app.py` | Process entry point. Configures logging and starts Uvicorn using the configured API host and port. |
| `src/api.py` | FastAPI app and lifecycle. Initializes the Postgres checkpointer and agent; serves the static chat page and assets; implements `/health` and `/api/chat`; formats model output and handles approval/resume requests. |
| `src/config.py` | Pydantic settings loaded from `.env` and environment variables: app mode, database, LLM provider/API keys, and API host/port. |
| `.env.example` | Safe template for local configuration. Copy to `.env` and add local credentials there; never commit `.env`. |
| `pyproject.toml` | Project metadata, runtime dependencies, and optional `dev` and `rag` dependency groups. |
| `uv.lock` | Exact dependency resolution used by `uv sync`. |
| `requirements.txt` | Exported runtime dependency list for pip-compatible environments. `pyproject.toml` and `uv.lock` are the primary `uv` dependency definitions. |
| `requirements-venv.txt` | Legacy machine-specific snapshot; incomplete for this project and not the portable dependency source. Use `requirements.txt`. |
| `.gitignore` | Keeps local secrets, virtual environments, generated data, caches, and other non-source files out of Git. |

### Agent

| File | Responsibility |
|---|---|
| `src/agent/graph.py` | Builds the LangGraph workflow: agent node, tool node, and routing between them. |
| `src/agent/nodes.py` | Creates and caches the provider-specific chat model, binds tools, retrieves style examples, invokes the model, and routes tool calls. |
| `src/agent/prompts.py` | System prompts for demo and personal modes, including response behavior and financial data guidance. |
| `src/agent/state.py` | Defines the conversation state using LangGraph `MessagesState`. |
| `src/agent/style_rag.py` | Searches the local SQLite FTS5 index for up to two relevant conversation examples to guide response style. |
| `src/agent/__init__.py` | Marks the agent module package. |

### Database and tools

| File | Responsibility |
|---|---|
| `src/db/connection.py` | Opens and manages PostgreSQL connections for database tools. |
| `src/db/__init__.py` | Marks the database module package. |
| `src/tools/__init__.py` | Collects the individual LangChain tools into the `all_tools` list bound to the agent. |
| `src/tools/account_balance.py` | Looks up the current balance of an account. |
| `src/tools/all_accounts.py` | Lists accounts, balances, account types, and institutions. |
| `src/tools/recent_transactions.py` | Fetches recent transaction history. |
| `src/tools/spending_by_category.py` | Summarizes monthly spending by category. |
| `src/tools/finance_query.py` | Runs custom read-only `SELECT` queries for questions not covered by specialized tools. |
| `src/tools/create_transaction.py` | Prepares and records a transaction after an explicit LangGraph confirmation. |
| `src/tools/update_transaction.py` | Updates a transaction after an explicit LangGraph confirmation. |
| `src/tools/delete_transaction.py` | Deletes a transaction after an explicit LangGraph confirmation. |
| `src/tools/block_account.py` | Requests admin approval before marking the active customer's exact bank account inactive. |
| `src/tools/request_manual_service_review.py` | Sends unsupported high-impact requests to specialists for follow-up, or records restricted requests for review only, without modifying customer data. |
| `src/tools/generate_chart.py` | Queries financial data and creates chart images. |

### Local chat page

| File | Responsibility |
|---|---|
| `web/index.html` | Chat page structure and accessibility labels. |
| `web/assets/chat.css` | Standalone responsive chat styling. |
| `web/assets/chat.js` | Calls the same-origin API, renders messages, tracks the thread ID, and handles pending write confirmations. |

### Scripts, tests, and generated data

| File or path | Responsibility |
|---|---|
| `scripts/seed_demo_db.sql` | Creates the demo PostgreSQL schema and inserts sample banking data. |
| `scripts/ingest_support_dataset.py` | Downloads selected training examples from Hugging Face and builds the local SQLite FTS5 style index. |
| `tests/test_api.py` | Tests API request validation, agent invocation, structured model output formatting, and write-approval flow. |
| `tests/test_style_rag.py` | Tests style retrieval and behavior when no local index exists. |
| `data/support_style.sqlite3` | Generated, Git-ignored search index built from Hugging Face dialogue examples; can be recreated with the ingestion script. |
| `data/bank_policies_*.jsonl` | Source policy documents; included in Git and imported to rebuild the local Chroma index. |
| `data/chroma/` and generated CSV exports | Local generated index and verification output; excluded from Git. |

`src/__init__.py`, `src/agent/__init__.py`, `src/db/__init__.py`, `src/tools/__init__.py`, and `tests/__init__.py` mark their folders as Python packages. The package initializers that export tools also define the relevant package-level public interface.

## Data responsibilities

PostgreSQL is queried live by the agent's tools for account and transaction facts; these facts are not copied into the style index. The Hugging Face customer-support conversations are retrieved from SQLite full-text search as dialogue examples. The current prompt and index treat those examples as **style guidance only**, not as verified policies or actual customer information. A real policy and product knowledge base would be a separate data source and ingestion flow.

To index the 5,000-example Hugging Face pilot:

```powershell
uv sync --extra rag
uv run python scripts/ingest_support_dataset.py
```

The local SQLite index is stored under the ignored `data/` directory. The agent retrieves up to two matching examples as guidance for tone and conversation flow. Factual account data comes from PostgreSQL tools; use a separate approved and maintained source for bank policies and product information.
