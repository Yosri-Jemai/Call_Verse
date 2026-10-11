from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from src import api


class FakeAgent:
    def __init__(self, result, pending=False):
        self.result = result
        self.pending = pending
        self.invocations = []

    def get_state(self, config):
        interrupts = (object(),) if self.pending else ()
        return SimpleNamespace(tasks=[SimpleNamespace(interrupts=interrupts)])

    def invoke(self, value, config):
        self.invocations.append((value, config))
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
    agent = FakeAgent({"messages": [SimpleNamespace(content="Approved.")]}, pending=True)

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
