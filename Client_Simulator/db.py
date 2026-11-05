"""
Storage for the Digital Twin: PostgreSQL (recommended) or SQLite (zero-setup fallback).

    DATABASE_URL=postgresql://user:password@localhost:5432/client_sim     -> PostgreSQL
    DATABASE_URL=sqlite:///client_simulator.db   (or unset)               -> SQLite file

What is stored:
  crm_clients / crm_contracts  synthetic CRM the Customer Advisor can query (same ids as the callers)
  runs                         one row per simulation (policy, seed, difficulty, scenario...) or live call
  incidents                    random incidents of a run (outage, fraud alert...)
  calls / turns                every call with its ground-truth labels, and its transcript
  kpis                         KPIs per run, so fifo / priority / RL can be compared over time

Usage:
    python db.py init            # create the tables
    python db.py runs            # list the last runs and their KPIs
    python db.py clients 5       # show 5 synthetic CRM clients
Needs `pip install "psycopg[binary]"` for PostgreSQL. SQLite needs nothing.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
import uuid
from typing import Optional

DEFAULT_URL = "sqlite:///client_simulator.db"

# One schema, two dialects: {PK_AUTO}, {BOOL}, {REAL}, {TS} are replaced per backend.
SCHEMA = """
CREATE TABLE IF NOT EXISTS crm_clients (
    client_id   TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    city        TEXT,
    segment     TEXT,
    balance     {REAL},
    birth_date  TEXT,
    card_last4  TEXT,
    phone       TEXT
);
CREATE TABLE IF NOT EXISTS crm_contracts (
    id          {PK_AUTO},
    client_id   TEXT NOT NULL REFERENCES crm_clients(client_id) ON DELETE CASCADE,
    product     TEXT NOT NULL,
    status      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runs (
    run_id      TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    scenario    TEXT,
    policy      TEXT,
    seed        INTEGER,
    n_advisors  INTEGER,
    difficulty  {REAL},
    config      TEXT,
    created_at  {TS}
);
CREATE TABLE IF NOT EXISTS incidents (
    id          {PK_AUTO},
    run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    start_s     {REAL},
    end_s       {REAL},
    multiplier  {REAL}
);
CREATE TABLE IF NOT EXISTS calls (
    run_id            TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    call_id           TEXT NOT NULL,
    client_id         TEXT,
    profile           TEXT,
    intent            TEXT,
    language          TEXT,
    incident          TEXT,
    is_fraudster      {BOOL},
    fraud_strategies  TEXT,
    status            TEXT,
    turns             INTEGER,
    waited_s          {REAL},
    satisfaction      {REAL},
    resolved          {BOOL},
    escalated         {BOOL},
    abandoned         {BOOL},
    identity_verified {BOOL},
    verification_asked {BOOL},
    fraud_served_unverified {BOOL},
    wrong_claims      INTEGER,
    sensitive_requests INTEGER,
    final_emotion     TEXT,
    mood_trace        TEXT,
    PRIMARY KEY (run_id, call_id)
);
CREATE TABLE IF NOT EXISTS turns (
    id          {PK_AUTO},
    run_id      TEXT NOT NULL,
    call_id     TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL,
    FOREIGN KEY (run_id, call_id) REFERENCES calls(run_id, call_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS kpis (
    run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    metric      TEXT NOT NULL,
    value       {REAL},
    PRIMARY KEY (run_id, metric)
);
CREATE INDEX IF NOT EXISTS idx_calls_run ON calls(run_id);
CREATE INDEX IF NOT EXISTS idx_turns_call ON turns(run_id, call_id, idx);
"""

DIALECTS = {
    "postgres": {"PK_AUTO": "BIGSERIAL PRIMARY KEY", "BOOL": "BOOLEAN", "REAL": "DOUBLE PRECISION",
                 "TS": "TIMESTAMPTZ NOT NULL DEFAULT now()"},
    "sqlite": {"PK_AUTO": "INTEGER PRIMARY KEY AUTOINCREMENT", "BOOL": "INTEGER", "REAL": "REAL",
               "TS": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP"},
}


class Store:
    """Thin wrapper over psycopg (PostgreSQL) or sqlite3. One connection, guarded by a lock
    (FastAPI runs sync endpoints in a thread pool)."""

    def __init__(self, url: Optional[str] = None):
        url = url or os.getenv("DATABASE_URL") or DEFAULT_URL
        self.url = url
        self._lock = threading.RLock()
        if url.startswith(("postgresql://", "postgres://")):
            try:
                import psycopg
            except ImportError as e:
                raise RuntimeError('PostgreSQL needs the driver: pip install "psycopg[binary]"') from e
            self.backend = "postgres"
            self._conn = psycopg.connect(url)
        else:
            path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else url
            self.backend = "sqlite"
            self._conn = sqlite3.connect(path, check_same_thread=False)
            self._conn.execute("PRAGMA foreign_keys = ON")
        self.init_schema()

    # ---- plumbing ---------------------------------------------------------
    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.backend == "postgres" else sql

    def _run(self, sql: str, params=(), *, many: bool = False, fetch: bool = False):
        with self._lock:
            cur = self._conn.cursor()
            try:
                if many:
                    cur.executemany(self._sql(sql), params)
                else:
                    cur.execute(self._sql(sql), params)
                rows = None
                if fetch:
                    cols = [d[0] for d in cur.description]
                    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
                self._conn.commit()
                return rows
            except Exception:
                self._conn.rollback()
                raise
            finally:
                cur.close()

    def init_schema(self) -> None:
        ddl = SCHEMA
        for k, v in DIALECTS[self.backend].items():
            ddl = ddl.replace("{" + k + "}", v)
        for stmt in [s.strip() for s in ddl.split(";") if s.strip()]:
            self._run(stmt)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ---- CRM (what the Customer Advisor queries) ---------------------------
    def upsert_clients(self, records) -> None:
        """records: iterable of CRM dicts (ClientPersona.crm_record())."""
        records = list(records)
        if not records:
            return
        self._run(
            "INSERT INTO crm_clients (client_id, name, city, segment, balance, birth_date, card_last4, phone) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (client_id) DO UPDATE SET "
            "name = excluded.name, city = excluded.city, segment = excluded.segment, balance = excluded.balance, "
            "birth_date = excluded.birth_date, card_last4 = excluded.card_last4, phone = excluded.phone",
            [(r["client_id"], r["name"], r.get("city"), r.get("segment"), r.get("balance"),
              r.get("birth_date"), r.get("card_last4"), r.get("phone")) for r in records], many=True)
        ids = [(r["client_id"],) for r in records]
        self._run("DELETE FROM crm_contracts WHERE client_id = ?", ids, many=True)
        self._run("INSERT INTO crm_contracts (client_id, product, status) VALUES (?, ?, ?)",
                  [(r["client_id"], c["product"], c["status"]) for r in records for c in r.get("contracts", [])],
                  many=True)

    def get_client(self, client_id: str) -> Optional[dict]:
        rows = self._run("SELECT * FROM crm_clients WHERE client_id = ?", (client_id,), fetch=True)
        if not rows:
            return None
        rec = rows[0]
        rec["contracts"] = self._run("SELECT product, status FROM crm_contracts WHERE client_id = ? ORDER BY id",
                                     (client_id,), fetch=True)
        return rec

    # ---- runs -------------------------------------------------------------
    def create_run(self, kind: str, scenario: str = "", policy: str = "", seed: Optional[int] = None,
                   n_advisors: Optional[int] = None, difficulty: float = 0.0, config: Optional[dict] = None) -> str:
        run_id = uuid.uuid4().hex[:12]
        self._run("INSERT INTO runs (run_id, kind, scenario, policy, seed, n_advisors, difficulty, config) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                  (run_id, kind, scenario, policy, seed, n_advisors, difficulty, json.dumps(config or {})))
        return run_id

    def save_incidents(self, run_id: str, incidents) -> None:
        self._run("INSERT INTO incidents (run_id, name, start_s, end_s, multiplier) VALUES (?, ?, ?, ?, ?)",
                  [(run_id, i.name, i.start, i.end, i.mult) for i in incidents], many=True)

    def save_calls(self, run_id: str, summaries: list[dict], transcripts: Optional[dict] = None) -> None:
        """summaries: ClientSimulatorAgent.summary() dicts; transcripts: {request_id: [(role, text), ...]}."""
        transcripts = transcripts or {}
        self._run(
            "INSERT INTO calls (run_id, call_id, client_id, profile, intent, language, incident, is_fraudster, "
            "fraud_strategies, status, turns, waited_s, satisfaction, resolved, escalated, abandoned, "
            "identity_verified, verification_asked, fraud_served_unverified, wrong_claims, sensitive_requests, final_emotion, mood_trace) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [(run_id, s["request_id"], s.get("client_id"), s["profile"], s["intent"], s.get("language"),
              s.get("incident", ""), bool(s["is_fraudster"]), json.dumps(s.get("fraud_strategies", [])),
              s["status"], s["turns"], s["waited_s"], s["satisfaction"], bool(s["resolved"]),
              bool(s["escalated"]), bool(s["abandoned"]), bool(s.get("identity_verified", False)),
              bool(s.get("verification_asked", False)), bool(s.get("fraud_served_unverified", False)), s.get("wrong_claims", 0), s.get("sensitive_requests", 0),
              s.get("final_emotion", ""), json.dumps(s.get("mood_trace", []))) for s in summaries], many=True)
        rows = [(run_id, cid, i, role, text) for cid, hist in transcripts.items() for i, (role, text) in enumerate(hist)]
        if rows:
            self._run("INSERT INTO turns (run_id, call_id, idx, role, text) VALUES (?, ?, ?, ?, ?)", rows, many=True)

    def save_kpis(self, run_id: str, kpis: dict) -> None:
        self._run("INSERT INTO kpis (run_id, metric, value) VALUES (?, ?, ?) ON CONFLICT (run_id, metric) "
                  "DO UPDATE SET value = excluded.value",
                  [(run_id, k, float(v)) for k, v in kpis.items() if isinstance(v, (int, float))], many=True)

    # ---- reading ----------------------------------------------------------
    def list_runs(self, limit: int = 20, kind: Optional[str] = None) -> list[dict]:
        where, params = ("WHERE kind = ?", (kind, limit)) if kind else ("", (limit,))
        runs = self._run(f"SELECT * FROM runs {where} ORDER BY created_at DESC LIMIT ?", params, fetch=True)
        for r in runs:
            r["created_at"] = str(r["created_at"])
            r["kpis"] = self.run_kpis(r["run_id"])
        return runs

    def run_kpis(self, run_id: str) -> dict:
        return {r["metric"]: r["value"] for r in
                self._run("SELECT metric, value FROM kpis WHERE run_id = ?", (run_id,), fetch=True)}

    def run_calls(self, run_id: str, limit: int = 200) -> list[dict]:
        return self._run("SELECT * FROM calls WHERE run_id = ? LIMIT ?", (run_id, limit), fetch=True)

    def run_incidents(self, run_id: str) -> list[dict]:
        return self._run("SELECT name, start_s, end_s, multiplier FROM incidents WHERE run_id = ? ORDER BY start_s",
                         (run_id,), fetch=True)

    def transcript(self, run_id: str, call_id: str) -> list[dict]:
        return self._run("SELECT idx, role, text FROM turns WHERE run_id = ? AND call_id = ? ORDER BY idx",
                         (run_id, call_id), fetch=True)

    def live_call_transcript(self, call_id: str) -> Optional[dict]:
        """Return persisted conversation turns and metadata for a live call."""
        rows = self._run(
            """
            SELECT c.run_id, c.call_id, c.profile, c.intent, c.language, c.status,
                   c.waited_s, c.resolved, c.escalated, c.identity_verified,
                   c.verification_asked, c.is_fraudster, r.kind
            FROM calls c
            JOIN runs r ON r.run_id = c.run_id
            WHERE c.call_id = ? AND r.kind = 'live'
            ORDER BY r.created_at DESC
            LIMIT 1
            """,
            (call_id,),
            fetch=True,
        )
        if not rows:
            return None
        call = rows[0]
        call["transcript"] = self.transcript(call["run_id"], call_id)
        return call

    def list_clients(self, limit: int = 20) -> list[dict]:
        return self._run("SELECT * FROM crm_clients LIMIT ?", (limit,), fetch=True)


def store_or_none(url: Optional[str] = None):
    """For code that should keep working when the database is down: (store, error_message)."""
    try:
        return Store(url), None
    except Exception as e:                       # driver missing, server down, bad password...
        return None, f"{type(e).__name__}: {e}"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "init"
    s = Store()
    print(f"connected: {s.backend}")
    if cmd == "init":
        print("tables ready")
    elif cmd == "runs":
        for r in s.list_runs(10):
            print(r["run_id"], r["kind"], r["scenario"], r["policy"], "diff=", r["difficulty"], r["kpis"])
    elif cmd == "clients":
        for c in s.list_clients(int(sys.argv[2]) if len(sys.argv) > 2 else 5):
            print(c)
