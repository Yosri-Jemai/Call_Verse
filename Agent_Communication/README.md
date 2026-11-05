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
email when explicitly requested. Manual mode lets you write or dictate an advisor
response.

The admin page is a local-demo page and does not have sign-in or role-based
access control. Do not expose it or the APIs on a public network.

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
