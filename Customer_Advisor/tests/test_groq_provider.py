from src import config
from src.agent import nodes
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage


def test_create_model_configures_groq_openai_compatible_endpoint(monkeypatch):
    constructor_calls = {}

    class FakeChatOpenAI:
        def __init__(self, **kwargs):
            constructor_calls.update(kwargs)

        def bind_tools(self, tools):
            constructor_calls["tools"] = tools
            return "configured-groq-model"

    monkeypatch.setattr(config.settings, "llm_provider", "groq")
    monkeypatch.setattr(config.settings, "groq_api_key", "gsk_test_key")
    monkeypatch.setattr(config.settings, "model_name", "")
    monkeypatch.setattr(nodes, "all_tools", [])
    monkeypatch.setattr("langchain_openai.ChatOpenAI", FakeChatOpenAI)

    model = nodes.create_model()

    assert model == "configured-groq-model"
    assert constructor_calls["model"] == "openai/gpt-oss-120b"
    assert constructor_calls["api_key"] == "gsk_test_key"
    assert constructor_calls["base_url"] == "https://api.groq.com/openai/v1"


def test_groq_history_trimming_keeps_latest_turn_and_its_tool_results():
    messages = [
        HumanMessage(content="old question " * 300),
        AIMessage(content="old answer " * 300),
        HumanMessage(content="latest question"),
        AIMessage(content="", tool_calls=[{
            "name": "lookup",
            "args": {},
            "id": "call-1",
            "type": "tool_call",
        }]),
        ToolMessage(content="latest tool result", tool_call_id="call-1"),
    ]

    trimmed = nodes._trim_groq_history(messages)

    assert trimmed == messages[2:]


def test_groq_history_trimming_keeps_recent_turns_within_budget():
    messages = [
        HumanMessage(content="first question"),
        AIMessage(content="first answer"),
        HumanMessage(content="second question"),
        AIMessage(content="second answer"),
    ]

    assert nodes._trim_groq_history(messages) == messages
