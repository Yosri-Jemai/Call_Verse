# Start the project (Windows)

Run these commands in PowerShell from the `Customer_Advisor` project folder.

## 1. Create a virtual environment and install dependencies

Do this once for a fresh checkout:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-venv.txt
```

## 2. Configure the application

Create `.env` from the example if you do not already have one:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

Edit `.env` and set your LLM provider and its API key, and the PostgreSQL connection
settings (`DB_HOST`, `DB_PORT`, `DB_USER`, and `DB_PASSWORD`). The application always
uses the `cashy_banking` database, regardless of `APP_MODE`. Keep `.env` private; do not commit it.

## 3. Set up the demo database

Make sure PostgreSQL is installed and its server is running. In a PostgreSQL-enabled
PowerShell terminal, create and seed the demo database:

```powershell
createdb -U postgres cashy_banking
psql -U postgres -d cashy_banking -f scripts/seed_demo_db.sql
```

If your PostgreSQL user or password is different, use the matching values in these
commands and in `.env`.

## 4. Start the application

```powershell
.\.venv\Scripts\python.exe app.py
```

Keep this terminal open while using the app. Open
<http://127.0.0.1:8000/> in your browser. Press `Ctrl+C` in the terminal to stop it.

If port 8000 is already in use, select another port before starting:

```powershell
$env:API_PORT = "8010"
.\.venv\Scripts\python.exe app.py
```

Then open <http://127.0.0.1:8010/>.
