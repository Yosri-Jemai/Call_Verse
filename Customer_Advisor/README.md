# Cashy Agent Service

Cashy is a Python banking-support agent backend intended to be called by a separate Next.js application. It uses LangGraph and PostgreSQL tools, provides an HTTP API, and includes a tiny standalone browser chat page for local testing.

> **Prototype safety:** The API is for local testing with demo data. It has no caller authentication or customer-level authorization. Do not expose it to the internet or load real customer data until identity verification, authorization, audit logging, and sensitive-data controls are implemented. Never ingest passwords, PINs, CVVs, full card numbers, or one-time codes.

## Run locally on Windows

Requirements: Python 3.10+ and a running PostgreSQL server. The commands below use
PowerShell from the `Customer_Advisor` project directory.

### Create a virtual environment and install dependencies

For a fresh checkout without an existing virtual environment:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-venv.txt
Copy-Item .env.example .env
```

`requirements-venv.txt` is a pinned snapshot of the project's dependencies. Alternatively,
use `uv sync --extra rag` to install from the project dependency definitions.

Edit `.env` and provide an LLM key, for example:

```env
APP_MODE=demo
LLM_PROVIDER=google
GOOGLE_API_KEY=your_key_here
DB_HOST=localhost
DB_PORT=5432
DB_NAME_DEMO=cashy_banking_demo
DB_USER=postgres
DB_PASSWORD=
```

Create the dedicated demo database in PostgreSQL, then load its schema and sample records:

```powershell
createdb -U postgres cashy_banking_demo
psql -U postgres -d cashy_banking_demo -f scripts/seed_demo_db.sql
```

After completing the database setup above, start the API:

```powershell
.\.venv\Scripts\python.exe app.py
```

This command keeps the server running in the current terminal; press `Ctrl+C` to stop it.
The service binds to `http://127.0.0.1:8000` by default. Open that address for the
standalone chat page. If port 8000 is already in use, choose another port for this PowerShell
session, for example `$env:API_PORT = "8010"`, before running the command, then open
`http://127.0.0.1:8010`. PostgreSQL must permit the configured local authentication method;
set `DB_PASSWORD` if your server requires a password.

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

The response includes a `thread_id`; send it with later messages to continue the same conversation. Write tools return `pending_approval: true` and a `confirmation` object before changing data. To resume, send the same thread ID and an `approved` boolean:

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
| `requirements-venv.txt` | Snapshot of packages installed in one local virtual environment; useful for reference, but not the portable project dependency source of truth. |
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
| `data/` | Local generated indexes and data files; excluded from Git. |

`src/__init__.py`, `src/agent/__init__.py`, `src/db/__init__.py`, `src/tools/__init__.py`, and `tests/__init__.py` mark their folders as Python packages. The package initializers that export tools also define the relevant package-level public interface.

## Data responsibilities

PostgreSQL is queried live by the agent's tools for account and transaction facts; these facts are not copied into the style index. The Hugging Face customer-support conversations are retrieved from SQLite full-text search as dialogue examples. The current prompt and index treat those examples as **style guidance only**, not as verified policies or actual customer information. A real policy and product knowledge base would be a separate data source and ingestion flow.

To index the 5,000-example Hugging Face pilot:

```powershell
uv sync --extra rag
uv run python scripts/ingest_support_dataset.py
```

The local SQLite index is stored under the ignored `data/` directory. The agent retrieves up to two matching examples as guidance for tone and conversation flow. Factual account data comes from PostgreSQL tools; use a separate approved and maintained source for bank policies and product information.
