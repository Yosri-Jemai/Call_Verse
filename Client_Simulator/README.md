# Client Simulator Agent: Digital Twin of a banking call center

Role: represent the customer, talk to the Customer Advisor, react to its answers, and output
KPIs and ground truth for the Quality Analyst and the Workforce Manager.

## What is new in this version
| # | Upgrade | Where |
|---|---|---|
| 1 | **LLM-driven client** with a hidden goal, secret CRM facts and an evolving emotion. Rules stay as the fallback. | `client_simulator.py`, `persona.py` |
| 2 | **Adaptive fraudster**: 5 strategies x 3 pressure levels, switches strategy when refused, knows only part of the victim's data (leak level 0-2). | `client_simulator.py`, `banks.py` |
| 3 | **French / Darija / mix** (Tunisian Arabic in Latin script). The LLM code-switches by itself. | `banks.py`, prompt in `client_simulator.py` |
| 4 | **Realistic load**: hourly profile, random incidents (app outage, ATM outage, fraud alert, flash campaign), difficulty 0-1 and a `Curriculum` for RL training. | `load_model.py` |
| 5 | **LLM judge** for resolution / empathy / tone / identity checks, with negation-safe rules as fallback. | `judge.py` |
| + | **PostgreSQL** (SQLite fallback): synthetic CRM, runs, calls, transcripts, KPIs. | `db.py` |
| + | **No ground-truth leak**: the advisor receives an `AdvisorView`, not the hidden state. | `client_simulator.py` |

## Files
| File | Purpose |
|---|---|
| client_simulator.py | The agent: profiles, intents, scenarios, conversation engine, `AdvisorView` |
| persona.py | Hidden persona: synthetic CRM facts, what the caller really knows, claim checking |
| judge.py | Reads the advisor's message (LLM judge or negation-safe rules) |
| load_model.py | Hourly profile, incidents, difficulty, `Curriculum` |
| banks.py | Rule-based lines in French and Darija, fraud ladders |
| advisors.py | Scripted advisors: `baseline_advisor` (verifies identity), `naive_advisor` (never does) |
| db.py | PostgreSQL / SQLite storage |
| llm_backends.py | `claude_llm()`, `gemini_llm()`, `ollama_llm()`, `extract_json()` |
| sim_env.py | SimPy environment: queue, advisors, policies (fifo / priority), KPIs, optional DB save |
| graph.py | LangGraph agent: init > advisor > perceive > update > speak > finish (see ARCHITECTURE.md) |
| audio.py | Whisper (speech to text) and gTTS (text to speech) |
| api.py + frontend/ | FastAPI backend and Next.js UI |
| test_client_simulator.py | Tests (25) |

## Install and run
    pip install -r requirements.txt        # Whisper also needs ffmpeg
    python client_simulator.py              # demo, no dependency
    pytest -q                               # tests (SQLite, no server needed)
    python sim_env.py                       # fifo vs priority on 3 scenarios + a curriculum demo
    python graph.py                         # one call through LangGraph

## PostgreSQL
1. Create the database (once):

       createdb -U postgres client_sim          # or in pgAdmin: Create > Database > client_sim

2. Point the project at it. Windows (cmd): `set DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/client_sim`
   PowerShell: `$env:DATABASE_URL="postgresql://postgres:YOUR_PASSWORD@localhost:5432/client_sim"`
   Linux / macOS: `export DATABASE_URL=postgresql://postgres:YOUR_PASSWORD@localhost:5432/client_sim`
3. Create the tables and check the connection: `python db.py init`
4. Run a simulation that saves itself:

       from db import Store
       from sim_env import CallCenterEnv
       env = CallCenterEnv(scenario="panne", policy="priority", difficulty=0.5, store=Store())
       env.run(); print(env.run_id, env.kpis())

   Then `python db.py runs`, or `SELECT * FROM kpis WHERE run_id = '...'` in pgAdmin.

Without `DATABASE_URL` the project uses a local SQLite file, so nothing breaks. The API reports the
active backend (and any connection error) at `GET /api/health`.

Tables: `crm_clients`, `crm_contracts` (the CRM the advisor can query), `runs`, `incidents`, `calls`,
`turns` (transcripts), `kpis`.

## Use a real LLM (check this first)
    cp .env.example .env        # then put ANTHROPIC_API_KEY (or GEMINI_API_KEY) in it
    python llm_backends.py check   # real round trip: tells you exactly what is wrong
    python graph.py                # one call; prints "client engine: claude / ..." and the node trace
`GET /api/llm/status?test=true` does the same from the API. The UI shows
`Client engine: <backend> · N LLM turns, M rule-based` and the reason when the LLM was not used.
Default backend is now `auto` (first configured one); `llm_strict=True` makes failures raise.

    from client_simulator import ClientSimulatorAgent
    from llm_backends import claude_llm        # needs ANTHROPIC_API_KEY
    sim = ClientSimulatorAgent(seed=1, llm=claude_llm(), judge_llm=claude_llm(max_tokens=300, temperature=0))

`gemini_llm()` and `ollama_llm()` work the same way. `llm` plays the client and `judge_llm` judges the
advisor; use a cheap model for the judge. Any failure (network, bad JSON) falls back to the rules and is
counted in `sim.stats["llm_failed"]`. The client answers with JSON (`reply`, `emotion`, `mood_adjust`,
`hang_up`). `mood_adjust` is clamped to +-0.15, so the LLM nuances the mood but cannot take over the score.

## How identity checking works
Every caller is backed by a synthetic CRM record. A fraudster impersonates an existing client and knows only
part of it:

| leak level | the fraudster knows |
|---|---|
| 0 | the name |
| 1 | + phone number |
| 2 | + birth date (the card digits are never known) |

The simulator extracts the facts stated in the client's text and compares them with the CRM, in code, so it
does not depend on what the LLM "claims". `identity_verified` is true when at least 2 facts are right and
none is wrong. An advisor that asks only for phone + birth date is fooled by a leak-2 fraudster; one that also
asks for the card digits is not. That is measurable in the KPIs.

## Interface with the other agents
- **Output to the queue (Workforce Manager):** `ClientRequest` (urgency, arrival_time, patience, ...).
- **Input from the Customer Advisor:** any function `AdvisorView -> str`. The advisor sees only the history,
  the caller id and `crm_lookup(client_id)` (backed by PostgreSQL when a Store is used). It never sees the
  profile, the intent, the mood or `is_fraudster`. This replaces the old `ConversationState -> str`, which let
  an advisor read the ground truth.
- **Output to Quality Analyst / KPIs:** `sim.summary(st)`: status, wait, satisfaction, `is_fraudster`,
  `fraud_strategies`, `identity_verified`, `verification_asked`, `sensitive_requests`, `mood_trace`, ...
- `is_fraudster` is hidden ground truth: never show it to the advisor.
- **Fraud KPIs:** a fraudster the advisor serves counts as `fraud_leaked`, whether the CRM check was fooled
  or skipped (`fraud_leaked_unverified`). Previously a careless advisor could never leak fraud, because the
  simulator refused to serve a fraudster that had not been verified.

## Realistic load and curriculum
    sim.generate_requests("panne", 120, difficulty=0.5, start_hour=8)   # peaks, incidents, harder clients
    sim.incidents                                                       # the incidents of that workload
    from load_model import Curriculum
    cur = Curriculum(start=0.1, end=1.0, target=0.8)                    # linear: cur.level_at(episode)
    cur.update(1 - kpis["abandon_rate"])                                # adaptive: raise difficulty when the agent copes

`realistic=False` restores the constant Poisson rate.

## Web interface (Next.js + FastAPI)

    Browser (Next.js, :3000)  <-- JSON / audio -->  FastAPI (api.py, :8000)  -->  the Python modules

**Terminal 1: backend**

    pip install -r requirements.txt
    uvicorn api:app --reload --port 8000        # docs: http://localhost:8000/docs

**Terminal 2: frontend** (Node.js 18+)

    cd frontend
    npm install
    npm run dev                                 # open http://localhost:3000

New API options: `POST /api/calls` accepts `language` (auto | fr | mix | darija), `difficulty` and `judge_llm`;
`POST /api/simulate` accepts `difficulty`, `start_hour`, `advisor` (baseline | naive) and `save`.
New endpoints: `GET /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/calls/{call_id}`, `GET /api/crm/{client_id}`.
The UI itself has not been updated yet (no language / difficulty selector).

## Notes and limits
- Darija lines in `banks.py` were written by an AI: have a native speaker review them. Whisper has no
  Tunisian model (auto-detect, expect errors on heavy Darija) and gTTS cannot read Latin-script Darija well.
- Verified in the authoring sandbox: all 25 tests, the demo, and the SQL on a real PostgreSQL 16 server.
- **Not run against the real packages** (no pip access there): SimPy (`sim_env.py` ran against a small
  stand-in), LangGraph (`graph.py`), FastAPI / pydantic (endpoint functions ran against stubs), `psycopg`,
  Whisper, gTTS, the LLM backends and the Next.js frontend. Run `pytest`, `python sim_env.py` and
  `python db.py init` once on your machine.
