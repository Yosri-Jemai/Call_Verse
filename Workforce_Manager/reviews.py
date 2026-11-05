"""
Human review queue: requests that a person must APPROVE or DENY before anything is changed.

Stored in SQLite (standard library, no server needed). Default file: Workforce_Manager/data/reviews.db.
Every decision is written to an audit trail (who, when, what, note). A review can be decided once.

The simulator's hidden ground truth (was the caller a fraudster?) is kept in a separate table that
the reviewer never sees; it is only read by the reports to measure how good the decisions were.
"""
from __future__ import annotations

import functools
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DEFAULT_DB = Path(__file__).resolve().parent / "data" / "reviews.db"

PENDING, APPROVED, DENIED = "pending", "approved", "denied"

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
    review_id      TEXT PRIMARY KEY,
    created_at     TEXT NOT NULL,
    run_id         TEXT,
    call_id        TEXT,
    client_id      TEXT,
    client_name    TEXT,
    category       TEXT NOT NULL,
    categories     TEXT,
    blocking       INTEGER NOT NULL,
    fields         TEXT,
    requested      TEXT,
    risk_score     INTEGER,
    signals        TEXT,
    identity_check TEXT,
    intent         TEXT,
    advisor        TEXT,
    advisor_skill  TEXT,
    call_outcome   TEXT,
    evidence       TEXT,
    status         TEXT NOT NULL DEFAULT 'pending',
    decided_by     TEXT,
    decided_at     TEXT,
    note           TEXT
);
CREATE INDEX IF NOT EXISTS reviews_status ON reviews(status);
CREATE INDEX IF NOT EXISTS reviews_run ON reviews(run_id);
CREATE TABLE IF NOT EXISTS review_audit (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id  TEXT NOT NULL,
    at         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT
);
CREATE TABLE IF NOT EXISTS review_hidden (
    review_id  TEXT PRIMARY KEY,
    at         TEXT NOT NULL,
    by         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_ground_truth (
    review_id     TEXT PRIMARY KEY,
    is_fraudster  INTEGER
);
"""

_JSON_COLS = ("categories", "fields", "signals")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _locked(method):
    """One SQLite connection shared by the API's worker threads: serialize access."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class ReviewStore:
    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- write -------------------------------------------------------------
    @_locked
    def add(self, *, category: str, categories: list, blocking: bool, run_id: str = "", call_id: str = "",
            client_id: str = "", client_name: str = "", fields: Optional[list] = None, requested: str = "",
            risk_score: int = 0, signals: Optional[list] = None, identity_check: str = "", intent: str = "",
            advisor: str = "", advisor_skill: str = "", call_outcome: str = "", evidence: str = "",
            is_fraudster: Optional[bool] = None) -> str:
        review_id = "WFM-" + uuid.uuid4().hex[:8].upper()
        with self.conn:
            self.conn.execute(
                "INSERT INTO reviews (review_id, created_at, run_id, call_id, client_id, client_name, category, "
                "categories, blocking, fields, requested, risk_score, signals, identity_check, intent, advisor, "
                "advisor_skill, call_outcome, evidence, status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (review_id, _now(), run_id, call_id, client_id, client_name, category, json.dumps(categories),
                 int(blocking), json.dumps(fields or []), requested, int(risk_score), json.dumps(signals or []),
                 identity_check, intent, advisor, advisor_skill, call_outcome, evidence, PENDING))
            self.conn.execute("INSERT INTO review_audit (review_id, at, actor, action, detail) VALUES (?,?,?,?,?)",
                              (review_id, _now(), "workforce_manager", "flagged",
                               f"{category}: {requested or call_outcome}"))
            if is_fraudster is not None:
                self.conn.execute("INSERT INTO review_ground_truth (review_id, is_fraudster) VALUES (?,?)",
                                  (review_id, int(is_fraudster)))
        return review_id

    @_locked
    def decide(self, review_id: str, approve: bool, by: str, note: str = "") -> dict:
        """Approve or deny a pending review. Raises ValueError if it does not exist or is already decided."""
        by = (by or "").strip()
        if not by:
            raise ValueError("a reviewer name is required (who takes the decision?)")
        with self.conn:
            cur = self.conn.execute(
                "UPDATE reviews SET status = ?, decided_by = ?, decided_at = ?, note = ? "
                "WHERE review_id = ? AND status = ?",
                (APPROVED if approve else DENIED, by, _now(), note, review_id, PENDING))
            if cur.rowcount == 0:
                existing = self.get(review_id)
                if existing is None:
                    raise ValueError(f"review {review_id} not found")
                raise ValueError(f"review {review_id} is already {existing['status']} "
                                 f"(by {existing['decided_by']}): a decision cannot be changed")
            action = "approved" if approve else "denied"
            detail = note
            if approve:
                rv = self.get(review_id)
                if rv and rv["blocking"]:
                    detail = (note + " | " if note else "") + f"change released for execution: {rv['requested']}"
            self.conn.execute("INSERT INTO review_audit (review_id, at, actor, action, detail) VALUES (?,?,?,?,?)",
                              (review_id, _now(), by, action, detail))
        return self.get(review_id)

    @_locked
    def hide_decided(self, review_ids: list[str], by: str) -> int:
        """Clear the history: decided reviews disappear from the page, the signed decision and the
        audit trail stay in the database. Pending reviews are never hidden."""
        by = (by or "").strip() or "dashboard"
        n = 0
        with self.conn:
            for rid in review_ids:
                rv = self.get(rid)
                if rv is None or rv["status"] == PENDING:
                    continue
                cur = self.conn.execute("INSERT OR IGNORE INTO review_hidden (review_id, at, by) VALUES (?,?,?)",
                                        (rid, _now(), by))
                if cur.rowcount:
                    n += 1
                    self.conn.execute("INSERT INTO review_audit (review_id, at, actor, action, detail) "
                                      "VALUES (?,?,?,?,?)", (rid, _now(), by, "hidden", "history cleared"))
        return n

    @_locked
    def hidden_ids(self) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT review_id FROM review_hidden")}

    def approve(self, review_id: str, by: str, note: str = "") -> dict:
        return self.decide(review_id, True, by, note)

    def deny(self, review_id: str, by: str, note: str = "") -> dict:
        return self.decide(review_id, False, by, note)

    # ---- read --------------------------------------------------------------
    @staticmethod
    def _row(row: Optional[sqlite3.Row]) -> Optional[dict]:
        if row is None:
            return None
        d = dict(row)
        for k in _JSON_COLS:
            d[k] = json.loads(d[k]) if d.get(k) else []
        d["blocking"] = bool(d["blocking"])
        return d

    @_locked
    def get(self, review_id: str) -> Optional[dict]:
        return self._row(self.conn.execute("SELECT * FROM reviews WHERE review_id = ?", (review_id,)).fetchone())

    @_locked
    def list(self, status: Optional[str] = None, run_id: Optional[str] = None, limit: int = 1000,
             run_prefix: Optional[str] = None, call_id: Optional[str] = None) -> list[dict]:
        sql, args = "SELECT * FROM reviews WHERE 1=1", []
        if status:
            sql += " AND status = ?"
            args.append(status)
        if run_id:
            sql += " AND run_id = ?"
            args.append(run_id)
        if run_prefix:
            sql += " AND run_id LIKE ?"
            args.append(run_prefix + "%")
        if call_id:
            sql += " AND call_id = ?"
            args.append(call_id)
        sql += " ORDER BY created_at, review_id LIMIT ?"
        args.append(limit)
        return [self._row(r) for r in self.conn.execute(sql, args).fetchall()]

    @_locked
    def audit(self, review_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            "SELECT at, actor, action, detail FROM review_audit WHERE review_id = ? ORDER BY id", (review_id,))]

    @_locked
    def truth(self, review_id: str) -> Optional[bool]:
        """Evaluation only (reports / simulated call-back): never shown to the reviewer."""
        row = self.conn.execute("SELECT is_fraudster FROM review_ground_truth WHERE review_id = ?",
                                (review_id,)).fetchone()
        return None if row is None or row[0] is None else bool(row[0])

    @_locked
    def stats(self, run_id: Optional[str] = None) -> dict:
        reviews = self.list(run_id=run_id, limit=1_000_000)
        out = dict(total=len(reviews), pending=0, approved=0, denied=0, by_category={},
                   fraud_approved=0, fraud_denied=0, genuine_approved=0, genuine_denied=0)
        for r in reviews:
            out[r["status"]] += 1
            out["by_category"][r["category"]] = out["by_category"].get(r["category"], 0) + 1
            t = self.truth(r["review_id"])
            if t is not None and r["status"] != PENDING:
                out[f"{'fraud' if t else 'genuine'}_{r['status']}"] += 1
        return out

    def close(self) -> None:
        self.conn.close()
