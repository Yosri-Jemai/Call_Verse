import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from google.genai.errors import APIError as GoogleAPIError
from langchain_core.messages import HumanMessage
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from pydantic import BaseModel, Field, model_validator

from src.agent.graph import create_agent
from src.config import settings
from src.stt.transcriber import transcribe_audio_file

logger = logging.getLogger("cashy.api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SERVICE_BUSY_DETAIL = "Le service est temporairement saturé, réessayez plus tard."
LLM_UNAVAILABLE_DETAIL = "Le service de génération est indisponible."
INTERNAL_ERROR_DETAIL = "Erreur interne."


class ChatRequest(BaseModel):
    message: str | None = Field(default=None, min_length=1, max_length=8000)
    thread_id: UUID | None = None
    customer_id: int | None = Field(default=None, ge=1)
    approved: bool | None = None

    @model_validator(mode="after")
    def require_message_or_approval(self):
        if self.approved is None and (self.message is None or not self.message.strip()):
            raise ValueError("Provide a non-empty message or an approval decision.")
        if self.approved is not None and self.thread_id is None:
            raise ValueError("A thread_id is required to approve or reject an action.")
        return self


class ChatResponse(BaseModel):
    thread_id: UUID
    message: str | None = None
    pending_approval: bool = False
    confirmation: dict[str, Any] | None = None


class TranscriptionResponse(BaseModel):
    text: str
    language: str
    segments: list[dict[str, Any]]


MAX_AUDIO_SIZE = 25 * 1024 * 1024
SUPPORTED_AUDIO_TYPES = {
    "audio/flac",
    "audio/mp3",
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/wav",
    "audio/webm",
    "audio/x-wav",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
        checkpointer.setup()
        app.state.agent = create_agent(checkpointer=checkpointer)
        logger.info("Agent ready [mode=%s db=%s]", settings.app_mode, settings.resolved_db_name)
        yield


app = FastAPI(
    title="Cashy Agent API",
    description=(
        "Local development API for the Cashy banking-support agent. "
        "This prototype has no caller authentication or customer-level authorization."
    ),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


def format_result(thread_id: UUID, result: dict[str, Any]) -> ChatResponse:
    interrupts = result.get("__interrupt__", ())
    if interrupts:
        confirmation = interrupts[0].value
        return ChatResponse(
            thread_id=thread_id,
            message=confirmation.get("message", "Please review and confirm this action."),
            pending_approval=True,
            confirmation=confirmation,
        )

    messages = result.get("messages", ())
    if not messages:
        raise RuntimeError("The agent returned no response.")

    content = messages[-1].content
    if isinstance(content, str):
        response_text = content
    elif isinstance(content, list):
        text_parts = [
            block["text"]
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        response_text = "\n".join(text_parts) if text_parts else json.dumps(content, default=str)
    elif isinstance(content, dict) and isinstance(content.get("text"), str):
        response_text = content["text"]
    else:
        response_text = json.dumps(content, default=str)
    return ChatResponse(thread_id=thread_id, message=response_text)


@app.get("/health")
def health():
    return {"status": "ok", "mode": settings.app_mode}


@app.post("/api/transcribe", response_model=TranscriptionResponse)
def transcribe_audio(
    audio: UploadFile = File(...),
    language: str | None = Form(default=None),
) -> TranscriptionResponse:
    content_type = (audio.content_type or "").split(";", maxsplit=1)[0].strip().lower()
    if content_type not in SUPPORTED_AUDIO_TYPES:
        raise HTTPException(status_code=415, detail="Unsupported audio format.")

    audio_bytes = audio.file.read(MAX_AUDIO_SIZE + 1)
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="The audio recording is empty.")
    if len(audio_bytes) > MAX_AUDIO_SIZE:
        raise HTTPException(status_code=413, detail="Audio must be 25 MB or smaller.")

    try:
        result = transcribe_audio_file(audio_bytes, language=language)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ImportError as exc:
        logger.exception("Speech-to-text dependency is unavailable")
        raise HTTPException(
            status_code=503,
            detail="Speech recognition is unavailable. Install the project dependencies and restart the app.",
        ) from exc
    except Exception as exc:
        logger.exception("Audio transcription failed")
        raise HTTPException(status_code=500, detail="Audio transcription failed.") from exc

    if not result["text"].strip():
        raise HTTPException(status_code=422, detail="No speech was detected in the recording.")
    return TranscriptionResponse(**result)


def _exception_chain(error: Exception):
    pending = [error]
    seen = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)


def _is_quota_or_rate_limit_error(error: Exception) -> bool:
    for current in _exception_chain(error):
        status_code = getattr(current, "status_code", None)
        status_code = status_code or getattr(current, "code", None)
        if status_code == 429:
            return True

        message = str(current).lower()
        if any(
            marker in message
            for marker in (
                "resource_exhausted",
                "rate limit",
                "rate_limit",
                "too many requests",
                "quota",
            )
        ):
            return True
    return False


def _is_llm_error(error: Exception) -> bool:
    llm_error_types = (ChatGoogleGenerativeAIError, GoogleAPIError)
    llm_provider_modules = (
        "openai",
        "anthropic",
        "google.genai",
        "langchain_google_genai",
        "langchain_openai",
        "langchain_anthropic",
        "huggingface_hub",
        "langchain_huggingface",
    )
    return any(
        isinstance(current, llm_error_types)
        or type(current).__module__.startswith(llm_provider_modules)
        or any(
            marker in type(current).__name__.lower()
            for marker in ("llm", "generativeai", "ratelimit", "resourceexhausted")
        )
        for current in _exception_chain(error)
    )


@app.get("/", include_in_schema=False)
def chat_ui():
    return FileResponse(WEB_DIR / "index.html")


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    try:
        agent = request.app.state.agent
        thread_id = payload.thread_id or uuid4()

        if payload.customer_id is not None:
            settings.current_customer_id = payload.customer_id

        customer_id = settings.current_customer_id
        config = {"configurable": {"thread_id": str(thread_id), "customer_id": str(customer_id)}}

        if payload.approved is not None:
            snapshot = agent.get_state(config)
            has_pending_interrupt = any(task.interrupts for task in snapshot.tasks)
            if not has_pending_interrupt:
                raise HTTPException(
                    status_code=409,
                    detail="This conversation has no pending action to approve or reject.",
                )
            result = agent.invoke(Command(resume={"approved": payload.approved}), config)
        else:
            if payload.message is None:
                raise HTTPException(status_code=422, detail="A message is required.")
            result = agent.invoke(
                {"messages": [HumanMessage(content=payload.message)]},
                config,
            )

        return format_result(thread_id, result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Chat request failed")
        if _is_quota_or_rate_limit_error(exc):
            raise HTTPException(status_code=429, detail=SERVICE_BUSY_DETAIL) from exc
        if _is_llm_error(exc):
            raise HTTPException(status_code=502, detail=LLM_UNAVAILABLE_DETAIL) from exc
        raise HTTPException(status_code=500, detail=INTERNAL_ERROR_DETAIL) from exc
