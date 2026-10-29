import json
import importlib
from contextlib import contextmanager

from src.config import settings

# Load the agent's tool registry before importing a tool submodule.
import src.agent.graph  # noqa: F401

profile_tool_module = importlib.import_module("src.tools.update_customer_profile")


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.query = ""
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params):
        self.query = query
        self.params = params

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, cursor):
        self.test_cursor = cursor

    def cursor(self):
        return self.test_cursor


@contextmanager
def fake_connection(cursor):
    yield FakeConnection(cursor)


def test_profile_tool_updates_only_requested_fields_for_active_customer(monkeypatch):
    cursor = FakeCursor((7, "Amira Ben Salah", "amira@example.com"))
    monkeypatch.setattr(settings, "current_customer_id", 7)
    monkeypatch.setattr(
        profile_tool_module,
        "get_connection",
        lambda: fake_connection(cursor),
    )

    result = json.loads(
        profile_tool_module.update_customer_profile.invoke(
            {"name": "  Amira Ben Salah  ", "email": " amira@example.com "}
        )
    )

    assert result == {
        "success": True,
        "customer_id": 7,
        "name": "Amira Ben Salah",
        "email": "amira@example.com",
        "updated_fields": ["email", "name"],
    }
    assert "UPDATE customers" in cursor.query
    assert "name = %s" in cursor.query
    assert "email = %s" in cursor.query
    assert cursor.params == ["Amira Ben Salah", "amira@example.com", 7]


def test_profile_tool_rejects_invalid_email_without_database_access(monkeypatch):
    def fail_if_called():
        raise AssertionError("Invalid input must not reach the database.")

    monkeypatch.setattr(profile_tool_module, "get_connection", fail_if_called)

    result = json.loads(
        profile_tool_module.update_customer_profile.invoke({"email": "not-an-email"})
    )

    assert result["success"] is False
    assert result["error"] == "Provide a valid email address."


def test_profile_tool_requires_an_explicit_change(monkeypatch):
    def fail_if_called():
        raise AssertionError("An empty update must not reach the database.")

    monkeypatch.setattr(profile_tool_module, "get_connection", fail_if_called)

    result = json.loads(profile_tool_module.update_customer_profile.invoke({}))

    assert result["success"] is False
    assert "Provide a new name" in result["error"]
