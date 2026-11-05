# Relay: Client Simulator ↔ Customer Advisor

Relay is a Next.js dashboard for running the existing Client Simulator and
Customer Advisor together. It displays the conversation as it happens, lets you
choose the caller's scenario, starting mood, language, and difficulty, and
supports either advisor autopilot or manual replies.

## Launch the dashboard on Windows

From the repository root in PowerShell:

```powershell
.\Agent_Communication\launch_all.ps1
```

The launcher opens the Customer Advisor API, Client Simulator API, and Relay
dashboard in separate windows. If a project service is already healthy on its
configured port, the launcher reuses it instead of failing or starting a
duplicate. A different process using a required port still causes a clear
error. Open <http://127.0.0.1:3000>. Both Python virtual environments must
already exist, and frontend dependencies must be installed. Start the dashboard
services against test/demo banking data only. If Docker Desktop is running, the
launcher also starts the optional local Redis cache; without Docker, the agent
continues to work but policy searches are not cached.

The Relay opens on a sign-in page. Before the first login, apply the
administrator-account migration to the `cashy_banking` PostgreSQL database,
set a private `JWT_SECRET` of at least 32 random bytes in
`Customer_Advisor\.env`, and create an account:

```powershell
psql -U postgres -d cashy_banking -f .\Customer_Advisor\scripts\migrations\001_create_admin_users.sql
python -c "import secrets; print(secrets.token_urlsafe(48))"
# Copy the generated value to Customer_Advisor\.env as JWT_SECRET=...
.\Customer_Advisor\.venv\Scripts\python.exe .\Customer_Advisor\scripts\migrations\create_admin_user.py approver1 approver
```

The account-creation script prompts for the password without echoing it.
The approval inbox is available only to `approver` accounts; `viewer` accounts
can access the simulation dashboard but not approval decisions. This is a
frontend access gate; do not expose the demo APIs publicly while backend
authorization is still being completed.

To use different ports:

```powershell
.\Agent_Communication\launch_all.ps1 -AdvisorPort 8010 -ClientApiPort 8011 -UiPort 3001
```

### Install frontend dependencies once

```powershell
Set-Location .\Agent_Communication\frontend
npm install
```

### Run the services manually

Open separate PowerShell terminals from the repository root:

```powershell
Set-Location .\Customer_Advisor
.\.venv\Scripts\python.exe app.py
```

```powershell
Set-Location .\Client_Simulator
.\.venv\Scripts\python.exe -m uvicorn api:app --host 127.0.0.1 --port 8001
```

```powershell
Set-Location .\Agent_Communication\frontend
$env:NEXT_PUBLIC_ADVISOR_API_URL = "http://127.0.0.1:8000"
$env:NEXT_PUBLIC_CLIENT_API_URL = "http://127.0.0.1:8001"
npm run dev
```

If running the dashboard on a port other than `3000`, set `CORS_ORIGINS` in
both API terminals to include `http://127.0.0.1:<port>` and
`http://localhost:<port>`.

The advisor autopilot passes only the customer's utterance to Customer Advisor.
Scenario metadata, mood, fraud status, and other hidden simulator state stay in
the simulator. Financial actions pause for explicit human approval in the
dashboard; they do not execute while waiting. Review requests at
`http://127.0.0.1:3000/admin/approvals` (replace the port if you changed `UiPort`).
The inbox shows why the advisor requested the action and its proposed details;
an administrator must approve or decline before the paused conversation resumes.
Requests are stored in the Customer Advisor PostgreSQL database and survive
dashboard reloads. The advisor can update only the active customer's name and
email when explicitly requested. The deterministic `blocage_compte` scenario
always asks to block a bank account; the advisor queues this high-impact action
for review, and approval marks that account inactive in PostgreSQL. This is an
account block, not a card block. Manual mode lets you write or dictate an advisor
response.

The deterministic `enregistrement_depense` scenario exercises a proposed $42
grocery ledger entry and sends it through the existing transaction approval gate.
The deterministic `nouveau_client_politique` scenario always creates a new client
asking about the contactless-payment limit. Before starting the call, the
dashboard runs the Customer Advisor's fixed policy query twice through the normal
Redis-backed search path and verifies that the second lookup is served from cache.
The scenario requires Redis to be enabled and the matching policy collection to
be ingested; otherwise it reports an error instead of implying the cache test
succeeded.
Card disputes, card blocking, transfer requests, and similar services unsupported
by the demo database can be escalated as `manual_service_review`. Approving one
authorizes manual follow-up only; it does not perform that bank operation or
modify PostgreSQL. Transaction create/update/delete and account blocking remain
the only consequential actions implemented as PostgreSQL mutations after approval.
When an action pauses for approval, the advisor's customer-facing specialist-review
message appears in the conversation first; its concise operation summary is sent
separately to the inbox. Requests that bypass verification or involve credentials
can be marked review-only: they cannot be approved for execution.

The admin page is a local-demo page and does not have sign-in or role-based
access control. Do not expose it or the APIs on a public network.

After each dashboard call ends, Relay automatically asks the Client Simulator
API to run Quality Analyst. The report history and details are available on the
dedicated `/quality` page. Quality Analyst uses Groq via API, configured with
`GROQ_API_KEY` in `Client_Simulator/.env`; no local Mistral model is required.
It scores accuracy, compliance, resolution, empathy, and relevance on a weighted
0–10 scale. The score is computed in code and capped at 5/10 for a critical
error. Numeric claims with explicit units are compared against source/tool
evidence attached to the same advisor turn; without any evidence in the call,
the check emits a warning rather than inventing an error. This does not verify
every factual claim. Manual replies have no Cashy tool trace.

Recommendations are human coaching suggestions for the team and are not
automatically applied to Cashy. Reports and available call evidence are stored
in the dedicated PostgreSQL database configured by `QA_DATABASE_URL` in
`Client_Simulator/.env`. The five QA tables are created automatically. If that
variable is omitted, Quality Analyst falls back to
`Quality_Analyst/data/quality_reports.sqlite3` (SQLite). This setting is
independent of the Client Simulator's `DATABASE_URL` and Cashy's database.
Conversations and evidence are saved before the Groq call, so a failed review
can be retried without losing the transcript. The database includes the
`qa_conversations`, `qa_tours`, `qa_sources`, `qa_evaluations`, and `qa_liens`
tables. The integration uses the simulator's persisted transcript when
available, but does not read LangGraph's PostgreSQL checkpointer directly. See
[`Quality_Analyst/README.md`](../Quality_Analyst/README.md) for configuration
and limitations.

## Voice

The dashboard uses the speech endpoints already supplied by each project:

- Customer Advisor speech recognition: local Faster-Whisper at `/api/transcribe`.
- Client Simulator speech recognition: Whisper/Groq at `/api/audio/stt`.
- Customer Advisor text-to-speech: Edge TTS at `/api/tts`.
- Client Simulator text-to-speech: Edge TTS or gTTS at `/api/audio/tts`.

The browser asks for microphone permission only when you choose to record a
manual advisor response. Speech recognition requires the corresponding audio
dependencies and model/API configuration in that service. Generated voices play
automatically by default; turn this off in Speech Studio or use each transcript
message's listen control.

## Terminal simulation

The original terminal pipeline remains available:

```powershell
.\Client_Simulator\.venv\Scripts\python.exe .\Agent_Communication\run_simulation.py `
  --scenario panne `
  --mood -0.6 `
  --language fr
```

Run `.\Client_Simulator\.venv\Scripts\python.exe .\Agent_Communication\run_simulation.py --help`
for all command-line options.

To run a post-call Quality Analyst evaluation, configure `GROQ_API_KEY` in the
process environment and pass `--quality-analysis`. The report is saved to a
local SQLite database; optional flags `--quality-report` and `--quality-db`
select the JSON report and database paths. See
[`Quality_Analyst/README.md`](../Quality_Analyst/README.md) for setup and limits.
