from src import config
from src.agent import nodes


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
    assert constructor_calls["model"] == "llama-3.3-70b-versatile"
    assert constructor_calls["api_key"] == "gsk_test_key"
    assert constructor_calls["base_url"] == "https://api.groq.com/openai/v1"
