# Client Simulator ↔ Customer Advisor

This folder runs the existing Client Simulator as the customer and sends each
customer utterance to the existing Customer Advisor API. The advisor's response is
then fed back into the simulator. No agent source files are modified.

From the repository root, the root `Makefile` starts the Customer Advisor API and
automatically runs the Client Simulator agent against it in separate PowerShell windows:

```powershell
make start SCENARIO=panne MOOD=-0.6 LANGUAGE=fr
```

This requires GNU Make and both projects' virtual environments to already be installed.
Customer Advisor uses port 8000 by default; set `ADVISOR_PORT=8010` if that port is
already occupied. No browser UI or manual chat input is needed to run the two agents.
The Client Simulator Next.js UI remains available as a separate human-advisor demo,
but is not part of the automatic agent-to-agent run.

The simulator passes only the customer's utterance to the advisor; scenario labels,
starting mood, fraud status, and other hidden simulator state are not sent. Pending
financial actions are automatically declined in simulation mode so the run needs no
operator input and cannot approve a transaction. This is a local prototype, so keep
the API bound to localhost.

## Start Customer Advisor

In one PowerShell terminal:

```powershell
cd .\Customer_Advisor
.\.venv\Scripts\python.exe app.py
```

The default API URL is `http://127.0.0.1:8000`. If you use another port, pass it
with `--advisor-url`.

## Run a simulation

From the repository root, use the Client Simulator virtual environment:

```powershell
.\Client_Simulator\.venv\Scripts\python.exe .\Agent_Communication\run_simulation.py `
  --scenario panne `
  --mood -0.6 `
  --language fr
```

The starting mood must be between `-1` (very upset) and `1` (very satisfied).
Available scenarios and languages are listed by:

```powershell
.\Client_Simulator\.venv\Scripts\python.exe .\Agent_Communication\run_simulation.py --help
```

Useful options include:

- `--scenario`: simulator scenario, such as `normal`, `panne`, or `vishing_otp`.
- `--mood`: client starting mood from `-1` to `1`.
- `--language`: `auto`, `fr`, `darija`, or `en`.
- `--difficulty`: simulator difficulty from `0` to `1`.
- `--seed`: reproduce the generated customer.
- `--max-turns`: maximum advisor/customer exchanges.
- `--client-llm`: `auto` by default, or `rules`, `claude`, `gemini`, `groq`, or `ollama`.
- `--customer-id`: Customer Advisor database customer ID, default `1`.
- `--advisor-url`: Customer Advisor API base URL, default `http://127.0.0.1:8000`.

The `auto` client LLM option uses configured Client Simulator credentials and falls
back to its rules engine if no backend is configured. Configure the client LLM in
`Client_Simulator\.env` and the advisor's LLM and database in `Customer_Advisor\.env`
as usual.

The simulator uses synthetic caller records, while Customer Advisor tools access
the configured banking database. Run this only against test/demo banking data.
