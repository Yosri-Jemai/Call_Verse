import base64
import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from google.genai.errors import APIError as GoogleAPIError
from langchain_core.messages import HumanMessage
from langchain_google_genai.chat_models import ChatGoogleGenerativeAIError
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from pydantic import BaseModel, Field, model_validator

from src.agent.graph import create_agent
from src.agent.policy_rag import get_embeddings
from src.auth.router import router as auth_router
from src.config import settings
from src.db.connection import get_connection
from src.stt.transcriber import transcribe_audio_file
from src.tss.synthesizer import synthesize_async

logger = logging.getLogger("cashy.api")
WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SERVICE_BUSY_DETAIL = "Le service est temporairement saturé, réessayez plus tard."
LLM_UNAVAILABLE_DETAIL = "Le service de génération est indisponible."
INTERNAL_ERROR_DETAIL = "Erreur interne."
APPROVAL_ACTIONS = {"create_transaction", "update_transaction", "delete_transaction"}


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


class ApprovalCreateRequest(BaseModel):
    thread_id: UUID
    customer_id: int = Field(ge=1)
    confirmation: dict[str, Any]


class ApprovalDecisionRequest(BaseModel):
    approved: bool


class TranscriptionResponse(BaseModel):
    text: str
    language: str
    segments: list[dict[str, Any]]


MAX_AUDIO_SIZE = 25 * 1024 * 1024
APPROVAL_COLUMNS = """
    id, thread_id, customer_id, action, message, details, status,
    created_at, reviewed_at, result
"""
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
    get_embeddings()
    with PostgresSaver.from_conn_string(settings.database_url) as checkpointer:
        checkpointer.setup()
        ensure_approval_table()
        app.state.agent = create_agent(checkpointer=checkpointer)
        logger.info("Agent ready [mode=%s db=%s]", settings.app_mode, settings.resolved_db_name)
        yield


def ensure_approval_table() -> None:
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS admin_approval_requests (
                    id UUID PRIMARY KEY,
                    thread_id UUID NOT NULL,
                    customer_id INTEGER NOT NULL CHECK (customer_id > 0),
                    action TEXT NOT NULL CHECK (
                        action IN ('create_transaction', 'update_transaction', 'delete_transaction')
                    ),
                    message TEXT NOT NULL,
                    details JSONB NOT NULL DEFAULT '{}'::jsonb,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK (
                        status IN ('pending', 'processing', 'approved', 'declined', 'failed')
                    ),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMPTZ,
                    result JSONB
                )
                """
            )
            cur.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_admin_approval_pending_thread
                ON admin_approval_requests (thread_id)
                WHERE status IN ('pending', 'processing')
                """
            )


app = FastAPI(
    title="Cashy Agent API",
    description=(
        "Local development API for the Cashy banking-support agent. "
        "Administrator authentication is available; authorization is still being "
        "applied incrementally to the remaining application routes."
    ),
    lifespan=lifespan,
)
cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in cors_origins if origin.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)
app.include_router(auth_router)
app.mount("/assets", StaticFiles(directory=WEB_DIR / "assets"), name="assets")


def approval_view(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "id": str(row[0]),
        "thread_id": str(row[1]),
        "customer_id": row[2],
        "action": row[3],
        "message": row[4],
        "details": row[5],
        "status": row[6],
        "created_at": row[7].isoformat() if row[7] else None,
        "reviewed_at": row[8].isoformat() if row[8] else None,
        "result": row[9],
    }


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


@app.post("/api/tts", response_class=Response)
async def text_to_speech(
    text: str = Body(..., min_length=1, max_length=8000, media_type="text/plain"),
) -> Response:
    if not text.strip():
        raise HTTPException(status_code=422, detail="Text to speak cannot be empty.")

    try:
        result = await synthesize_async(text, lang="auto")
    except ImportError as exc:
        logger.exception("Text-to-speech dependency is unavailable")
        raise HTTPException(
            status_code=503,
            detail="Text-to-speech is unavailable. Install the project dependencies and restart the app.",
        ) from exc
    except Exception as exc:
        logger.exception("Text-to-speech synthesis failed")
        raise HTTPException(
            status_code=502, detail="Text-to-speech synthesis failed."
        ) from exc

    if not result["audio_b64"]:
        raise HTTPException(status_code=502, detail="Text-to-speech returned no audio.")
    audio_bytes = base64.b64decode(result["audio_b64"], validate=True)
    return Response(content=audio_bytes, media_type="audio/mpeg")


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


@app.get("/api/admin/approvals")
def list_admin_approvals(status: str = "pending") -> list[dict[str, Any]]:
    if status not in {"pending", "all"}:
        raise HTTPException(status_code=422, detail="status must be 'pending' or 'all'.")
    query = f"SELECT {APPROVAL_COLUMNS} FROM admin_approval_requests"
    if status == "pending":
        query += " WHERE status IN ('pending', 'processing')"
    query += " ORDER BY created_at DESC LIMIT 100"

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall()
    except Exception as exc:
        logger.exception("Could not load admin approval requests")
        raise HTTPException(status_code=503, detail="Approval inbox is unavailable.") from exc
    return [approval_view(row) for row in rows]


@app.get("/api/admin/approvals/{approval_id}")
def get_admin_approval(approval_id: UUID) -> dict[str, Any]:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {APPROVAL_COLUMNS} FROM admin_approval_requests WHERE id = %s",
                    (approval_id,),
                )
                row = cur.fetchone()
    except Exception as exc:
        logger.exception("Could not load admin approval request %s", approval_id)
        raise HTTPException(status_code=503, detail="Approval request status is unavailable.") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Approval request not found.")
    return approval_view(row)


@app.post("/api/admin/approvals", status_code=201)
def create_admin_approval(payload: ApprovalCreateRequest) -> dict[str, Any]:
    action = payload.confirmation.get("action")
    if action not in APPROVAL_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail="Only financial transaction actions can be sent for approval.",
        )

    message = payload.confirmation.get("message")
    if not isinstance(message, str) or not message.strip():
        raise HTTPException(status_code=422, detail="The advisor's approval explanation is required.")
    details = {
        key: payload.confirmation[key]
        for key in ("details", "current", "changes")
        if key in payload.confirmation
    }

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    INSERT INTO admin_approval_requests
                        (id, thread_id, customer_id, action, message, details)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (thread_id)
                    WHERE status IN ('pending', 'processing')
                    DO UPDATE SET thread_id = EXCLUDED.thread_id
                    RETURNING {APPROVAL_COLUMNS}
                    """,
                    (
                        uuid4(),
                        payload.thread_id,
                        payload.customer_id,
                        action,
                        message.strip(),
                        json.dumps(details, ensure_ascii=False),
                    ),
                )
                row = cur.fetchone()
    except Exception as exc:
        logger.exception("Could not send advisor action to the admin inbox")
        raise HTTPException(status_code=503, detail="Could not send the action to the approval inbox.") from exc
    if row is None:
        raise HTTPException(status_code=503, detail="Approval request was not saved.")
    return approval_view(row)


@app.post("/api/admin/approvals/{approval_id}/decision")
def review_admin_approval(
    approval_id: UUID,
    payload: ApprovalDecisionRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    UPDATE admin_approval_requests
                    SET status = 'processing'
                    WHERE id = %s AND status = 'pending'
                    RETURNING {APPROVAL_COLUMNS}
                    """,
                    (approval_id,),
                )
                row = cur.fetchone()
                if row is None:
                    cur.execute(
                        "SELECT status FROM admin_approval_requests WHERE id = %s",
                        (approval_id,),
                    )
                    existing = cur.fetchone()
                    if existing is None:
                        raise HTTPException(status_code=404, detail="Approval request not found.")
                    raise HTTPException(
                        status_code=409,
                        detail=f"Approval request is already {existing[0]}.",
                    )
                approval = approval_view(row)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Could not claim admin approval request %s", approval_id)
        raise HTTPException(status_code=503, detail="Could not claim the approval request.") from exc

    try:
        response = chat(
            ChatRequest(
                thread_id=UUID(approval["thread_id"]),
                customer_id=approval["customer_id"],
                approved=payload.approved,
            ),
            request,
        )
    except HTTPException as exc:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE admin_approval_requests
                    SET status = 'failed', reviewed_at = CURRENT_TIMESTAMP,
                        result = %s::jsonb
                    WHERE id = %s AND status = 'processing'
                    """,
                    (json.dumps({"detail": exc.detail}, ensure_ascii=False), approval_id),
                )
        raise

    final_status = "pending" if response.pending_approval else (
        "approved" if payload.approved else "declined"
    )
    result = response.model_dump(mode="json")
    next_message = response.message or approval["message"]
    next_details = response.confirmation or approval["details"]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"""
                UPDATE admin_approval_requests
                SET status = %s, message = %s, details = %s::jsonb,
                    reviewed_at = CASE WHEN %s = 'pending' THEN NULL ELSE CURRENT_TIMESTAMP END,
                    result = %s::jsonb
                WHERE id = %s
                RETURNING {APPROVAL_COLUMNS}
                """,
                (
                    final_status,
                    next_message,
                    json.dumps(next_details, ensure_ascii=False),
                    final_status,
                    json.dumps(result, ensure_ascii=False),
                    approval_id,
                ),
            )
            updated = cur.fetchone()
    if updated is None:
        raise HTTPException(status_code=503, detail="Could not save the approval decision.")
    return approval_view(updated)
