import logging
from datetime import date
from langchain_core.messages import SystemMessage
from langgraph.graph import END

from src.agent.state import AgentState
from src.agent.prompts import get_system_prompt
from src.agent.style_rag import retrieve_style_examples
from src.config import settings
from src.tools import all_tools

logger = logging.getLogger("cashy.agent")

# Default models per provider
DEFAULT_MODELS = {
    "openai": "gpt-5-mini",
    "anthropic": "claude-sonnet-4-20250514",
    "google": "gemini-3.8-flash",
    "huggingface": "meta-llama/Llama-3.3-70B-Instruct",
    "free-tier": "Qwen/Qwen2.5-7B-Instruct",
}

# Capture Space's HF token at startup (before BYOK overwrites it)
_SPACE_HF_TOKEN = settings.hf_token


def create_model():
    """Create the LLM chat model with tools bound. Supports multiple providers."""
    provider = settings.resolved_provider
    if not provider:
        raise ValueError(
            "No API key configured. Please select a provider and enter your API key in the sidebar."
        )
    model_name = settings.model_name or DEFAULT_MODELS[provider]

    logger.info("Initializing LLM: %s (provider=%s)", model_name, provider)

    if provider == "openai":
        from langchain_openai import ChatOpenAI

        chat_model = ChatOpenAI(
            model=model_name,
            api_key=settings.openai_api_key,
            max_tokens=settings.model_max_tokens,
            temperature=settings.model_temperature,
        )

    elif provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        chat_model = ChatAnthropic(
            model=model_name,
            api_key=settings.anthropic_api_key,
            max_tokens=settings.model_max_tokens,
            temperature=settings.model_temperature,
        )

    elif provider == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        chat_model = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=settings.google_api_key,
            max_output_tokens=settings.model_max_tokens,
            temperature=settings.model_temperature,
        )

    elif provider == "free-tier":
        from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint

        model_name = DEFAULT_MODELS["free-tier"]  # always locked
        llm = HuggingFaceEndpoint(
            repo_id=model_name,
            task="text-generation",
            max_new_tokens=settings.model_max_tokens,
            huggingfacehub_api_token=_SPACE_HF_TOKEN,
        )
        chat_model = ChatHuggingFace(llm=llm)

    elif provider == "huggingface":
        from langchain_huggingface import ChatHuggingFace, HuggingFaceEndpoint

        llm = HuggingFaceEndpoint(
            repo_id=model_name,
            provider=settings.hf_inference_provider,
            task="text-generation",
            max_new_tokens=settings.model_max_tokens,
            huggingfacehub_api_token=settings.hf_token,
        )
        chat_model = ChatHuggingFace(llm=llm)

    else:
        raise ValueError(f"Unknown LLM provider: {provider}")

    tools = _sanitize_tools(all_tools) if provider in ("huggingface", "free-tier") else all_tools
    model = chat_model.bind_tools(tools)
    logger.info("Model ready with %d tools bound", len(all_tools))
    return model


# Module-level model instance (created once)
model_with_tools = None


def get_model():
    global model_with_tools
    if model_with_tools is None:
        model_with_tools = create_model()
    return model_with_tools


def reset_model():
    """Clear the cached model so the next call creates a fresh one."""
    global model_with_tools
    model_with_tools = None
    logger.info("Model cache cleared — next query will reinitialize")


def _sanitize_for_latin1(text: str) -> str:
    """Replace non-latin-1 Unicode characters for HuggingFace's HTTP transport."""
    result = []
    for c in text:
        try:
            c.encode("latin-1")
            result.append(c)
        except UnicodeEncodeError:
            # Common replacements
            if c in ("\u2014", "\u2013"):
                result.append("-")
            elif c in ("\u201c", "\u201d"):
                result.append('"')
            elif c in ("\u2018", "\u2019"):
                result.append("'")
            elif c == "\u2026":
                result.append("...")
            elif c == "\u2192":
                result.append("->")
            else:
                result.append("?")
    return "".join(result)


def _sanitize_tools(tools: list) -> list:
    """Return copies of tools with latin-1 safe descriptions."""
    import copy
    sanitized = []
    for tool in tools:
        t = copy.deepcopy(tool)
        if hasattr(t, "description"):
            t.description = _sanitize_for_latin1(t.description)
        if hasattr(t, "args_schema") and t.args_schema:
            for field_name, field_info in t.args_schema.model_fields.items():
                if field_info.description:
                    field_info.description = _sanitize_for_latin1(field_info.description)
        sanitized.append(t)
    return sanitized


def call_model(state: AgentState) -> dict:
    """Invoke the LLM with system prompt and tools."""
    model = get_model()
    today = date.today()
    prompt = get_system_prompt(settings.app_mode).format(today=today.isoformat(), year=today.year)
    messages = [SystemMessage(content=prompt)]

    latest_user_message = next(
        (message.content for message in reversed(state["messages"])
         if message.type == "human" and isinstance(message.content, str)),
        "",
    )
    style_examples = retrieve_style_examples(latest_user_message)
    if style_examples:
        messages.append(SystemMessage(content=style_examples))
    messages.extend(state["messages"])

    # HuggingFace Inference API requires latin-1 compatible text
    if settings.resolved_provider in ("huggingface", "free-tier"):
        logger.debug("Sanitizing %d messages for latin-1 compatibility", len(messages))
        for msg in messages:
            if isinstance(msg.content, str):
                msg.content = _sanitize_for_latin1(msg.content)

    logger.debug("Calling LLM (%d messages in state)", len(state["messages"]))

    response = model.invoke(messages)

    if response.tool_calls:
        tool_names = [tc["name"] for tc in response.tool_calls]
        logger.info("LLM requested tools: %s", ", ".join(tool_names))
        for tc in response.tool_calls:
            logger.debug("  -> %s(%s)", tc["name"], tc["args"])
    else:
        logger.info("LLM final response (%d chars)", len(response.content))

    return {"messages": [response]}


def should_continue(state: AgentState) -> str:
    """Route to tools if the model made tool calls, otherwise end."""
    last_message = state["messages"][-1]
    if last_message.tool_calls:
        logger.debug("Routing to tools node")
        return "tools"
    logger.debug("Routing to END")
    return END
