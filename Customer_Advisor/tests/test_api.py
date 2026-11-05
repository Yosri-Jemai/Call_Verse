import json
from types import SimpleNamespace
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from google.genai.errors import ClientError
from pydantic import ValidationError
from starlette.requests import Request

from src import api


class FakeAgent:
    def __init__(self, result=None, pending=False, error=None):
        self.result = result
        self.pending = pending
        self.error = error
        self.invocations = []

    def get_state(self, config):
        interrupts = (object(),) if self.pending else ()
        return SimpleNamespace(tasks=[SimpleNamespace(interrupts=interrupts)])

    def invoke(self, value, config):
        self.invocations.append((value, config))
        if self.error is not None:
            raise self.error
        return self.result


def make_request(agent):
    api.app.state.agent = agent
    return Request({"type": "http", "app": api.app})


def test_chat_request_requires_message_or_approval():
    with pytest.raises(ValidationError):
        api.ChatRequest()


def test_approval_requires_existing_thread_id():
    with pytest.raises(ValidationError):
        api.ChatRequest(approved=True)


def test_chat_invokes_agent_with_thread_and_returns_response():
    agent = FakeAgent({"messages": [SimpleNamespace(content="Your demo response.")]})
    thread_id = uuid4()

    response = api.chat(
        api.ChatRequest(message="Hello", thread_id=thread_id),
        make_request(agent),
    )

    assert response.thread_id == thread_id
    assert response.message == "Your demo response."
    assert response.pending_approval is False
    invocation, config = agent.invocations[0]
    assert invocation["messages"][0].content == "Hello"
    assert config["configurable"]["thread_id"] == str(thread_id)


def test_format_result_includes_bounded_policy_source_trace():
    policy_result = json.dumps({
        "found": True,
        "documents": [{
            "source": "fees.jsonl",
            "title": "Transfer fees",
            "category": "fees",
            "text": "Demo policy excerpt.",
            "source_url": "https://example.test/fees",
            "similarity_score": 0.91,
        }],
    })
    messages = [
        SimpleNamespace(type="human", content="How much does a transfer cost?", tool_calls=[]),
        SimpleNamespace(
            type="ai",
            content="",
            tool_calls=[{"name": "search_bank_policies"}],
        ),
        SimpleNamespace(
            type="tool",
            name="search_bank_policies",
            content=policy_result,
            tool_calls=[],
        ),
        SimpleNamespace(type="ai", content="The policy says...", tool_calls=[]),
    ]

    trace = api.extract_audit_trace(messages)

    assert trace["tools_called"] == ["search_bank_policies"]
    assert trace["policy_sources"][0]["source"] == "fees.jsonl"
    assert trace["policy_sources"][0]["text"] == "Demo policy excerpt."


def test_chat_returns_json_429_for_llm_quota_error(monkeypatch):
    quota_error = ClientError(
        429,
        {"error": {"status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded"}},
    )
    monkeypatch.setattr(api.settings, "llm_provider", "google")
    agent = FakeAgent(error=quota_error)
    make_request(agent)

    response = TestClient(api.app).post(
        "/api/chat",
        json={"message": "Hello"},
    )

    assert response.status_code == 429
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "detail": "Le service est temporairement saturé, réessayez plus tard."
    }


def test_transcribe_endpoint_returns_local_transcription(monkeypatch):
    expected = {
        "text": "Bonjour Cashy.",
        "language": "fr",
        "segments": [{"start": 0.0, "end": 1.5, "text": "Bonjour Cashy."}],
    }
    monkeypatch.setattr(api, "transcribe_audio_file", lambda audio_bytes, language=None: expected)

    response = TestClient(api.app).post(
        "/api/transcribe",
        files={"audio": ("recording.webm", b"audio-data", "audio/webm")},
    )

    assert response.status_code == 200
    assert response.json() == expected


def test_transcribe_endpoint_rejects_unsupported_audio_type():
    response = TestClient(api.app).post(
        "/api/transcribe",
        files={"audio": ("recording.txt", b"not audio", "text/plain")},
    )

    assert response.status_code == 415


def test_tts_endpoint_returns_synthesized_audio(monkeypatch):
    expected = {
        "audio_b64": "bXAzdGVzdA==",
        "voice": "fr-FR-DeniseNeural",
        "lang": "fr",
        "format": "mp3",
        "segments": [{"text": "Bonjour.", "lang": "fr"}],
        "words": ["Bonjour."],
        "wtimes": [0.0],
        "wdurations": [450.0],
    }

    async def fake_synthesize(text, lang="auto"):
        assert text == "**Bonjour.**"
        assert lang == "auto"
        return expected

    monkeypatch.setattr(api, "synthesize_async", fake_synthesize)
    response = TestClient(api.app).post(
        "/api/tts",
        content="**Bonjour.**",
        headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"mp3test"


def test_tts_endpoint_does_not_report_success_without_audio(monkeypatch):
    async def fake_synthesize(text, lang="auto"):
        return {
            "audio_b64": "",
            "voice": "fr-FR-DeniseNeural",
            "lang": "fr",
            "format": "mp3",
            "segments": [],
            "words": [],
            "wtimes": [],
            "wdurations": [],
        }

    monkeypatch.setattr(api, "synthesize_async", fake_synthesize)
    response = TestClient(api.app).post(
        "/api/tts",
        content="Bonjour.",
        headers={"Content-Type": "text/plain"},
    )

    assert response.status_code == 502
    assert response.json() == {"detail": "Text-to-speech returned no audio."}


def test_chat_returns_confirmation_for_pending_write():
    confirmation = {"action": "create_transaction", "message": "Confirm this?"}
    result = {"__interrupt__": [SimpleNamespace(value=confirmation)]}

    response = api.format_result(uuid4(), result)

    assert response.pending_approval is True
    assert response.confirmation == confirmation
    assert response.message == "Confirm this?"


def test_structured_model_response_returns_text_without_metadata():
    response = api.format_result(
        uuid4(),
        {
            "messages": [
                SimpleNamespace(
                    content=[
                        {
                            "type": "text",
                            "text": "Hello! How can I help?",
                            "extras": {"signature": "internal-signature"},
                        }
                    ]
                )
            ]
        },
    )

    assert response.message == "Hello! How can I help?"
    assert "signature" not in response.message


def test_approval_resumes_only_a_pending_action():
    thread_id = uuid4()
    agent = FakeAgent(
        {"messages": [SimpleNamespace(content="Approved.")]}, pending=True
    )

    response = api.chat(
        api.ChatRequest(thread_id=thread_id, approved=True),
        make_request(agent),
    )

    command, config = agent.invocations[0]
    assert command.resume == {"approved": True}
    assert config["configurable"]["thread_id"] == str(thread_id)
    assert response.message == "Approved."


def test_approval_without_pending_action_returns_conflict():
    agent = FakeAgent({"messages": []})

    with pytest.raises(HTTPException) as exc_info:
        api.chat(
            api.ChatRequest(thread_id=uuid4(), approved=False),
            make_request(agent),
        )

    assert exc_info.value.status_code == 409


class ApprovalCursor:
    def __init__(self, rows=(), one_rows=()):
        self.rows = rows
        self.one_rows = list(one_rows)
        self.query = ""
        self.params = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def execute(self, query, params=None):
        self.query = query
        self.params = params

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return self.one_rows.pop(0) if self.one_rows else None


class ApprovalConnection:
    def __init__(self, cursor):
        self.test_cursor = cursor

    def cursor(self):
        return self.test_cursor


@contextmanager
def approval_connection(cursor):
    yield ApprovalConnection(cursor)


def approval_row(approval_id, thread_id, status="pending", result=None):
    return (
        approval_id,
        thread_id,
        1,
        "create_transaction",
        "Create expense of $25.00?",
        {"details": {"amount": 25, "account": "Demo"}},
        status,
        datetime.now(timezone.utc),
        None,
        result,
    )


def test_create_admin_approval_saves_advisor_explanation(monkeypatch):
    approval_id = uuid4()
    thread_id = uuid4()
    cursor = ApprovalCursor(one_rows=[approval_row(approval_id, thread_id)])
    monkeypatch.setattr(api, "get_connection", lambda: approval_connection(cursor))

    response = api.create_admin_approval(
        api.ApprovalCreateRequest(
            thread_id=thread_id,
            customer_id=1,
            confirmation={
                "action": "create_transaction",
                "message": "Create expense of $25.00?",
                "details": {"amount": 25, "account": "Demo"},
            },
        )
    )

    assert response["id"] == str(approval_id)
    assert response["message"] == "Create expense of $25.00?"
    assert response["details"]["details"]["amount"] == 25
    assert "ON CONFLICT (thread_id)" in cursor.query
    assert isinstance(cursor.params[0], str)
    assert cursor.params[1] == str(thread_id)


def test_create_admin_approval_rejects_non_financial_actions(monkeypatch):
    def fail_if_called():
        raise AssertionError("Invalid approvals must not be written.")

    monkeypatch.setattr(api, "get_connection", fail_if_called)

    with pytest.raises(HTTPException) as exc_info:
        api.create_admin_approval(
            api.ApprovalCreateRequest(
                thread_id=uuid4(),
                customer_id=1,
                confirmation={"action": "update_customer_profile", "message": "Change name"},
            )
        )

    assert exc_info.value.status_code == 422


def test_admin_decision_resumes_paused_chat_and_records_result(monkeypatch):
    approval_id = uuid4()
    thread_id = uuid4()
    before = approval_row(approval_id, thread_id, status="processing")
    result_payload = api.ChatResponse(
        thread_id=thread_id,
        message="The expense was recorded.",
    )
    after = approval_row(
        approval_id,
        thread_id,
        status="approved",
        result=result_payload.model_dump(mode="json"),
    )
    cursors = [
        ApprovalCursor(one_rows=[before]),
        ApprovalCursor(one_rows=[after]),
    ]

    def next_connection():
        return approval_connection(cursors.pop(0))

    monkeypatch.setattr(api, "get_connection", next_connection)
    resumed = []

    def fake_chat(payload, request):
        resumed.append((payload, request))
        return result_payload

    monkeypatch.setattr(api, "chat", fake_chat)

    response = api.review_admin_approval(
        approval_id,
        api.ApprovalDecisionRequest(approved=True),
        make_request(FakeAgent()),
    )

    assert response["status"] == "approved"
    assert response["result"]["message"] == "The expense was recorded."
    assert resumed[0][0].thread_id == thread_id
    assert resumed[0][0].approved is True
    assert resumed[0][0].customer_id == 1
