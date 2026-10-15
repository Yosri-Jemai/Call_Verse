import json
from contextlib import contextmanager
from datetime import date

import pytest

from scripts.ingest_bank_policies import load_policy_documents
from src.tools import bank_policy_search_postgres as bank_policy_search


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.params = None

    def execute(self, query, params):
        self.query = query
        self.params = params

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, cursor):
        self.test_cursor = cursor

    def cursor(self):
        return self.test_cursor


@contextmanager
def fake_connection(cursor):
    yield FakeConnection(cursor)


def test_search_returns_policy_with_source_and_validity(monkeypatch):
    cursor = FakeCursor(
        [
            (
                "fees",
                "Transfer fees",
                "The approved fee text.",
                "Official tariff guide",
                "https://bank.example/fees",
                None,
                None,
            )
        ]
    )
    monkeypatch.setattr(
        bank_policy_search, "get_connection", lambda: fake_connection(cursor)
    )

    result = json.loads(bank_policy_search.search_bank_policies.invoke({"query": "frais virement"}))

    assert result["found"] is True
    assert result["documents"][0]["content"] == "The approved fee text."
    assert result["documents"][0]["source"] == "Official tariff guide"
    assert "websearch_to_tsquery('french'" in cursor.query
    assert cursor.params == ("frais virement", "frais virement", bank_policy_search.MAX_POLICY_RESULTS)


def test_search_reports_when_no_policy_matches(monkeypatch):
    cursor = FakeCursor([])
    monkeypatch.setattr(
        bank_policy_search, "get_connection", lambda: fake_connection(cursor)
    )

    result = json.loads(bank_policy_search.search_bank_policies.invoke({"query": "frais"}))

    assert result["found"] is False
    assert "No active bank-policy document" in result["message"]


def test_blank_query_does_not_open_database(monkeypatch):
    def fail_if_called():
        raise AssertionError("Database must not be queried for a blank question.")

    monkeypatch.setattr(bank_policy_search, "get_connection", fail_if_called)

    result = json.loads(bank_policy_search.search_bank_policies.invoke({"query": "  "}))

    assert result["found"] is False
    assert "empty" in result["message"]


def test_load_policy_documents_parses_required_and_optional_fields(tmp_path):
    input_file = tmp_path / "policies.jsonl"
    input_file.write_text(
        json.dumps(
            {
                "category": "fees",
                "title": "Transfers",
                "content": "Official fee text.",
                "source": "Tariff guide",
                "valid_from": "2026-01-01",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    documents = load_policy_documents(input_file)

    assert documents[0] == (
        "fees",
        "Transfers",
        "Official fee text.",
        "Tariff guide",
        None,
        date(2026, 1, 1),
        None,
    )


def test_load_policy_documents_rejects_missing_fields(tmp_path):
    input_file = tmp_path / "invalid.jsonl"
    input_file.write_text('{"title":"Transfers"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="missing fields"):
        load_policy_documents(input_file)


def test_load_policy_documents_rejects_invalid_dates(tmp_path):
    input_file = tmp_path / "invalid-date.jsonl"
    input_file.write_text(
        json.dumps(
            {
                "category": "fees",
                "title": "Transfers",
                "content": "Official fee text.",
                "source": "Tariff guide",
                "valid_from": "01/01/2026",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="YYYY-MM-DD"):
        load_policy_documents(input_file)
