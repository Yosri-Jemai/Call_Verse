from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

try:
    import psycopg
except ImportError:
    psycopg = None


class QualityStorageError(RuntimeError):
    """A Quality Analyst database operation failed."""


_DATABASE_ERRORS = (sqlite3.Error,) + ((psycopg.Error,) if psycopg else ())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _is_postgres(target: str | Path) -> bool:
    return str(target).startswith(("postgresql://", "postgres://"))


def _connect(target: str | Path):
    if _is_postgres(target):
        if psycopg is None:
            raise QualityStorageError(
                'PostgreSQL QA storage requires psycopg. Install it with '
                'pip install "psycopg[binary]".'
            )
        return psycopg.connect(str(target))
    connection = sqlite3.connect(target)
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _execute(connection, statement: str, parameters: tuple[Any, ...] = ()):
    if isinstance(connection, sqlite3.Connection):
        return connection.execute(statement, parameters)
    return connection.execute(statement.replace("?", "%s"), parameters)


def _ensure_schema(connection) -> None:
    is_sqlite = isinstance(connection, sqlite3.Connection)
    auto_id = "INTEGER PRIMARY KEY AUTOINCREMENT" if is_sqlite else "BIGSERIAL PRIMARY KEY"
    timestamp = (
        "TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))"
        if is_sqlite
        else "TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP"
    )
    statements = [
        f"""
        CREATE TABLE IF NOT EXISTS quality_reports (
            id {auto_id},
            call_id TEXT NOT NULL,
            created_at {timestamp},
            overall_score INTEGER,
            report_json TEXT NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS qa_conversations (
            conversation_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            call_id TEXT NOT NULL,
            run_id TEXT,
            thread_id TEXT,
            scenario TEXT,
            profil_client TEXT,
            motif TEXT,
            langue TEXT,
            issue TEXT,
            temps_attente_s DOUBLE PRECISION,
            duree_s DOUBLE PRECISION,
            cree_le {timestamp},
            metadata_json TEXT NOT NULL DEFAULT '{{}}',
            UNIQUE(source, call_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS qa_tours (
            conversation_id TEXT NOT NULL REFERENCES qa_conversations(conversation_id) ON DELETE CASCADE,
            tour INTEGER NOT NULL,
            role TEXT NOT NULL,
            texte TEXT NOT NULL,
            sources_citees TEXT NOT NULL DEFAULT '[]',
            outils TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY(conversation_id, tour, role)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS qa_sources (
            conversation_id TEXT NOT NULL REFERENCES qa_conversations(conversation_id) ON DELETE CASCADE,
            source_id TEXT NOT NULL,
            titre TEXT NOT NULL,
            extrait TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY(conversation_id, source_id)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS qa_evaluations (
            evaluation_id {auto_id},
            conversation_id TEXT NOT NULL REFERENCES qa_conversations(conversation_id) ON DELETE CASCADE,
            modele TEXT,
            run INTEGER NOT NULL DEFAULT 1,
            version_grille TEXT NOT NULL,
            score_global DOUBLE PRECISION,
            criteres TEXT NOT NULL,
            erreurs TEXT NOT NULL,
            explication TEXT NOT NULL,
            recommandations TEXT NOT NULL,
            avertissements TEXT NOT NULL,
            report_json TEXT NOT NULL,
            cree_le {timestamp}
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS qa_liens (
            call_id TEXT NOT NULL,
            thread_id TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'relay',
            PRIMARY KEY(call_id, thread_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_qa_evaluations_conversation
        ON qa_evaluations(conversation_id, evaluation_id DESC)
        """,
    ]
    for statement in statements:
        _execute(connection, statement)


def _source_id(source: dict[str, Any]) -> str:
    identity = "\n".join(str(source.get(key, "")) for key in ("source", "title", "text", "excerpt"))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _persist_conversation(
    connection,
    case: dict[str, Any],
    thread_id: str | None,
    run_id: str | None,
) -> tuple[str, str]:
    call_id = str(case.get("call_id") or case["conversation_id"])
    context = case.get("context", {})
    source = str(context.get("source", "relay"))
    metadata = {
        key: context.get(key)
        for key in ("profile", "intent", "language", "status", "resolved", "escalated", "waited_s", "duration_s")
        if context.get(key) is not None
    }
    _execute(
        connection,
        """
        INSERT INTO qa_conversations (
            conversation_id, source, call_id, run_id, thread_id, scenario,
            profil_client, motif, langue, issue, temps_attente_s, duree_s, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, call_id) DO UPDATE SET
            thread_id = COALESCE(excluded.thread_id, qa_conversations.thread_id),
            scenario = excluded.scenario,
            profil_client = excluded.profil_client,
            motif = excluded.motif,
            langue = excluded.langue,
            issue = excluded.issue,
            temps_attente_s = excluded.temps_attente_s,
            duree_s = excluded.duree_s,
            metadata_json = excluded.metadata_json
        """,
        (
            call_id,
            source,
            call_id,
            run_id or context.get("run_id"),
            thread_id or context.get("thread_id"),
            context.get("scenario"),
            context.get("profile"),
            context.get("intent"),
            context.get("language"),
            context.get("status"),
            context.get("waited_s"),
            context.get("duration_s"),
            _json(metadata),
        ),
    )
    row = _execute(
        connection,
        "SELECT conversation_id FROM qa_conversations WHERE source = ? AND call_id = ?",
        (source, call_id),
    ).fetchone()
    if row is None:
        raise QualityStorageError("QA conversation upsert did not persist a conversation.")
    conversation_id = row[0]
    _execute(connection, "DELETE FROM qa_tours WHERE conversation_id = ?", (conversation_id,))
    _execute(connection, "DELETE FROM qa_sources WHERE conversation_id = ?", (conversation_id,))

    advisor_traces = context.get("advisor_trace", [])
    traces_by_turn: dict[int, list[dict[str, Any]]] = {}
    all_sources: dict[str, dict[str, Any]] = {}
    for trace in advisor_traces:
        turn = trace.get("turn")
        if isinstance(turn, int):
            traces_by_turn.setdefault(turn, []).append(trace)
        for policy_source in trace.get("policy_sources", []):
            if isinstance(policy_source, dict):
                all_sources[_source_id(policy_source)] = policy_source

    for source_key, item in all_sources.items():
        _execute(
            connection,
            """
            INSERT INTO qa_sources (conversation_id, source_id, titre, extrait, metadata_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                source_key,
                str(item.get("title") or item.get("source") or "Source sans titre"),
                str(item.get("text") or item.get("excerpt") or ""),
                _json({
                    key: value for key, value in item.items()
                    if key not in {"title", "source", "text", "excerpt"}
                }),
            ),
        )

    for turn in case.get("transcript", []):
        turn_no = int(turn["turn"])
        traces = traces_by_turn.get(turn_no, []) if turn["role"] in {"conseiller", "advisor"} else []
        cited_sources = [
            _source_id(item)
            for trace in traces for item in trace.get("policy_sources", [])
            if isinstance(item, dict)
        ]
        tool_evidence = []
        for trace in traces:
            calls = trace.get("tool_calls", [])
            if calls:
                tool_evidence.extend(calls)
            else:
                tool_evidence.extend(trace.get("tool_outputs", []))
        _execute(
            connection,
            """
            INSERT INTO qa_tours (conversation_id, tour, role, texte, sources_citees, outils)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                conversation_id,
                turn_no,
                turn["role"],
                turn["text"],
                _json(cited_sources),
                _json(tool_evidence),
            ),
        )

    effective_thread_id = thread_id or context.get("thread_id")
    if effective_thread_id:
        _execute(
            connection,
            "INSERT INTO qa_liens (call_id, thread_id, source) VALUES (?, ?, ?) ON CONFLICT(call_id, thread_id) DO NOTHING",
            (call_id, str(effective_thread_id), source),
        )
    return conversation_id, call_id


def _save_case(
    connection,
    report: dict[str, Any],
    case: dict[str, Any],
    thread_id: str | None,
    run_id: str | None,
) -> int:
    conversation_id, _ = _persist_conversation(connection, case, thread_id, run_id)
    statement = """
        INSERT INTO qa_evaluations (
            conversation_id, modele, version_grille, score_global, criteres,
            erreurs, explication, recommandations, avertissements, report_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    parameters = (
        conversation_id,
        report.get("llm_judgment", {}).get("model"),
        report.get("version_grille", "1.0"),
        report.get("score_global"),
        _json(report.get("criteres", {})),
        _json(report.get("erreurs", [])),
        report.get("explication", ""),
        _json(report.get("recommendations", [])),
        _json(report.get("avertissements", [])),
        _json(report),
    )
    if isinstance(connection, sqlite3.Connection):
        cursor = _execute(connection, statement, parameters)
        evaluation_id = cursor.lastrowid
    else:
        evaluation_id = _execute(
            connection,
            statement.rstrip() + " RETURNING evaluation_id",
            parameters,
        ).fetchone()[0]
    if evaluation_id is None:
        raise QualityStorageError("Database did not return an ID for the QA evaluation.")
    return int(evaluation_id)


def _insert_legacy_report(connection, report: dict[str, Any]) -> int:
    statement = """
        INSERT INTO quality_reports (call_id, overall_score, report_json)
        VALUES (?, ?, ?)
    """
    parameters = (
        report["call_id"],
        report.get("overall_score_0_to_100"),
        _json(report),
    )
    if isinstance(connection, sqlite3.Connection):
        cursor = _execute(connection, statement, parameters)
        report_id = cursor.lastrowid
    else:
        report_id = _execute(
            connection,
            statement.rstrip() + " RETURNING id",
            parameters,
        ).fetchone()[0]
    if report_id is None:
        raise QualityStorageError("Database did not return an ID for the saved quality report.")
    return int(report_id)


def _database_errors_as_storage_error(operation: str, error: Exception) -> QualityStorageError:
    return QualityStorageError(f"Could not {operation} Quality Analyst data: {error}")


def save_conversation(
    case: dict[str, Any],
    database: str | Path,
    *,
    thread_id: str | None = None,
    run_id: str | None = None,
) -> None:
    if not _is_postgres(database):
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(_connect(database)) as connection:
            with connection:
                _ensure_schema(connection)
                _persist_conversation(connection, case, thread_id, run_id)
    except _DATABASE_ERRORS as exc:
        raise _database_errors_as_storage_error("save", exc) from exc


def save_report(
    report: dict[str, Any],
    database: str | Path,
    *,
    case: dict[str, Any] | None = None,
    thread_id: str | None = None,
    run_id: str | None = None,
) -> int:
    if not _is_postgres(database):
        Path(database).parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(_connect(database)) as connection:
            with connection:
                _ensure_schema(connection)
                if case is not None:
                    report_id = _save_case(connection, report, case, thread_id, run_id)
                    _insert_legacy_report(connection, report)
                else:
                    report_id = _insert_legacy_report(connection, report)
        return report_id
    except _DATABASE_ERRORS as exc:
        raise _database_errors_as_storage_error("store", exc) from exc


def list_reports(database: str | Path, limit: int = 50) -> list[dict[str, Any]]:
    if not _is_postgres(database) and not Path(database).exists():
        return []
    try:
        with closing(_connect(database)) as connection:
            with connection:
                _ensure_schema(connection)
                rows = _execute(
                    connection,
                    """
                    SELECT evaluation_id, conversation_id, cree_le, score_global, report_json
                    FROM qa_evaluations
                    ORDER BY evaluation_id DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
                if not rows:
                    rows = [
                        (report_id, call_id, created_at, score / 10 if score is not None else None, report_json)
                        for report_id, call_id, created_at, score, report_json in _execute(
                            connection,
                            """
                            SELECT id, call_id, created_at, overall_score, report_json
                            FROM quality_reports ORDER BY id DESC LIMIT ?
                            """,
                            (limit,),
                        ).fetchall()
                    ]
    except _DATABASE_ERRORS as exc:
        raise _database_errors_as_storage_error("read report history from", exc) from exc

    reports = []
    for report_id, call_id, created_at, score, report_json in rows:
        report = json.loads(report_json)
        judgment = report.get("llm_judgment", {})
        reports.append({
            "database_id": report_id,
            "call_id": call_id,
            "created_at": str(created_at),
            "scenario": report.get("scenario"),
            "score_global": score,
            "version_grille": report.get("version_grille"),
            "overall_score_0_to_100": (
                report.get("overall_score_0_to_100")
                if isinstance(judgment, dict) and judgment.get("model")
                else (score * 10 if score is not None else None)
            ),
            "summary": report.get("explication") or (
                judgment.get("summary") if isinstance(judgment, dict) else None
            ),
            "status": judgment.get("status") if isinstance(judgment, dict) else None,
        })
    return reports


def get_report(database: str | Path, report_id: int) -> dict[str, Any] | None:
    if not _is_postgres(database) and not Path(database).exists():
        return None
    try:
        with closing(_connect(database)) as connection:
            with connection:
                _ensure_schema(connection)
                row = _execute(
                    connection,
                    """
                    SELECT evaluation_id, cree_le, report_json
                    FROM qa_evaluations
                    WHERE evaluation_id = ?
                    """,
                    (report_id,),
                ).fetchone()
                if row is None:
                    row = _execute(
                        connection,
                        """
                        SELECT id, created_at, report_json
                        FROM quality_reports WHERE id = ?
                        """,
                        (report_id,),
                    ).fetchone()
    except _DATABASE_ERRORS as exc:
        raise _database_errors_as_storage_error("read report from", exc) from exc
    if row is None:
        return None
    stored_id, created_at, report_json = row
    report = json.loads(report_json)
    report["database_id"] = stored_id
    report["stored_at"] = str(created_at)
    return report
