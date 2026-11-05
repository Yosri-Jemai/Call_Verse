import importlib
import json
from contextlib import contextmanager

from src.config import settings

import src.agent.graph  # noqa: F401

block_account_module = importlib.import_module("src.tools.block_account")


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params):
        self.queries.append((query, params))

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None


class FakeConnection:
    def __init__(self, cursor):
        self.test_cursor = cursor

    def cursor(self):
        return self.test_cursor


@contextmanager
def fake_connection(cursor):
    yield FakeConnection(cursor)


def test_block_account_pauses_then_marks_only_the_active_customers_account_inactive(monkeypatch):
    lookup = FakeCursor([(42, "Checking", True)])
    update = FakeCursor([(42, "Checking")])
    cursors = [lookup, update]
    monkeypatch.setattr(settings, "current_customer_id", 9)
    monkeypatch.setattr(
        block_account_module,
        "get_connection",
        lambda: fake_connection(cursors.pop(0)),
    )
    interruptions = []

    def approve(confirmation):
        interruptions.append(confirmation)
        return {"approved": True}

    monkeypatch.setattr(block_account_module, "interrupt", approve)
    result = json.loads(
        block_account_module.block_account.invoke(
            {"account_name": "Checking", "reason": "The customer noticed suspicious activity."}
        )
    )

    assert result["success"] is True
    assert result["account_id"] == 42
    assert result["is_active"] is False
    assert interruptions == [
        {
            "action": "block_account",
            "message": "I’ll refer your account-block request for specialist approval. The account is not blocked yet.",
            "admin_message": "Block Checking: The customer noticed suspicious activity.",
            "details": {
                "account": "Checking",
                "reason": "The customer noticed suspicious activity.",
                "effect": "Marks the account inactive in PostgreSQL.",
            },
        }
    ]
    assert "customer_id = %s" in lookup.queries[0][0]
    assert lookup.queries[0][1] == ("Checking", 9)
    assert "SET is_active = FALSE" in update.queries[0][0]
    assert update.queries[0][1] == (42, 9)


def test_block_account_does_not_update_database_when_admin_declines(monkeypatch):
    lookup = FakeCursor([(42, "Checking", True)])
    monkeypatch.setattr(
        block_account_module,
        "get_connection",
        lambda: fake_connection(lookup),
    )
    monkeypatch.setattr(block_account_module, "interrupt", lambda _: {"approved": False})

    result = json.loads(
        block_account_module.block_account.invoke(
            {"account_name": "Checking", "reason": "Suspicious activity."}
        )
    )

    assert result["success"] is False
    assert "declined" in result["message"]
    assert len(lookup.queries) == 1


def test_block_account_rejects_unknown_account_without_requesting_approval(monkeypatch):
    lookup = FakeCursor([])
    monkeypatch.setattr(
        block_account_module,
        "get_connection",
        lambda: fake_connection(lookup),
    )

    def fail_if_called(_):
        raise AssertionError("An unknown account must not reach the approval interrupt.")

    monkeypatch.setattr(block_account_module, "interrupt", fail_if_called)

    result = json.loads(
        block_account_module.block_account.invoke(
            {"account_name": "Not an account", "reason": "Suspicious activity."}
        )
    )

    assert result["success"] is False
    assert "not found" in result["error"]
