"""
FastAPI backend for the Client Simulator.

Exposes the Python project (client_simulator, sim_env, audio, llm_backends) as a
JSON API for the Next.js frontend.

    uvicorn api:app --reload --port 8000

Interactive docs: http://localhost:8000/docs
"""
from __future__ import annotations

import os
import tempfile
import uuid
from collections import Counter
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from client_simulator import LANGUAGES, SCENARIOS, ClientSimulatorAgent, ConversationState
from db import store_or_none

app = FastAPI(title="Client Simulator API")

origins = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

# --------------------------------------------------------------------------
# Database: PostgreSQL if DATABASE_URL is set, otherwise a local SQLite file.
# If it cannot connect, the API keeps working without persistence (see /api/health).
# --------------------------------------------------------------------------
STORE, DB_ERROR = store_or_none()
PERSISTED: set[str] = set()

# --------------------------------------------------------------------------
# In-memory store of live calls (reset when the server restarts)
# --------------------------------------------------------------------------
CALLS: dict[str, tuple[ClientSimulatorAgent, ConversationState]] = {}
MAX_CALLS = 200
_LLM_CACHE: dict[tuple, object] = {}


def make_llm(name: Optional[str], role: str = "client", model: Optional[str] = None):
    """Return an llm(prompt)->str function, or None for the rule-based client.

    name: none | auto | claude | gemini | ollama.  "auto" takes the first backend that is really
    configured and falls back to rules (with the reason shown in the UI) when there is none.
    An explicit backend that cannot start is an error: no silent downgrade.
    """
    import llm_backends
    if not name or name == "none":
        return None
    key = (name, role, model)
    if key in _LLM_CACHE:
        return _LLM_CACHE[key]
    try:
        llm = llm_backends.auto_llm(role) if name == "auto" else llm_backends.make_backend(name, role, model)
    except llm_backends.LLMError as e:
        if name == "auto":
            _AUTO_REASON[role] = str(e)
            return None
        raise HTTPException(400, f"Cannot start the '{name}' backend: {e}")
    except Exception as e:  # unexpected SDK error
        raise HTTPException(400, f"Cannot start the '{name}' backend: {type(e).__name__}: {e}")
    _LLM_CACHE[key] = llm
    return llm


_AUTO_REASON: dict[str, str] = {}


def get_call(call_id: str) -> tuple[ClientSimulatorAgent, ConversationState]:
    if call_id not in CALLS:
        raise HTTPException(404, "Call not found (the server may have restarted). Start a new call.")
    return CALLS[call_id]


def view(call_id: str, sim: ClientSimulatorAgent, st: ConversationState) -> dict:
    """What the frontend is allowed to see. is_fraudster is revealed only once the call is over."""
    r = st.request
    out = {
        "call_id": call_id,
        "client": {
            "name": r.client_name, "profile": r.profile.value, "intent": r.intent,
            "urgency": r.urgency, "channel": r.channel, "patience_s": r.patience,
            "language": r.language, "client_id": r.client_id,
        },
        "status": st.status,
        "turn": st.turn,
        "waited_s": round(st.waited, 1),
        "mood": round(st.mood, 3),
        "emotion": st.emotion,
        "sentiment": st.sentiment,
        "satisfaction": st.satisfaction,
        "history": [{"role": role, "text": text} for role, text in st.history],
        "summary": None,
        # which engine really wrote the client's lines (no more guessing why it looks rule-based)
        "engine": {**sim.engine_report(st),
                   "warning": _AUTO_REASON.get("client", "") if sim.llm is None else ""},
    }
    if st.status != "active":
        out["summary"] = sim.summary(st)
        persist_call(call_id, sim, st)
    return out


def persist_call(call_id: str, sim: ClientSimulatorAgent, st: ConversationState) -> None:
    """Save a finished live call (transcript + ground-truth labels). Never breaks the call."""
    global DB_ERROR
    if STORE is None or call_id in PERSISTED:
        return
    try:
        run_id = STORE.create_run("live", st.request.profile.value, "human_advisor", None, 1, sim.difficulty,
                                  {"language": st.request.language})
        STORE.upsert_clients([sim.crm[st.request.client_id]])
        s = sim.summary(st)
        s["request_id"] = call_id
        STORE.save_calls(run_id, [s], {call_id: st.history})
        PERSISTED.add(call_id)
    except Exception as e:
        DB_ERROR = f"{type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "db": STORE.backend if STORE else None, "db_error": DB_ERROR}


@app.get("/api/llm/status")
def llm_status(test: bool = False):
    """Which LLM backends are configured. ?test=true also does one real round trip with the first ready one."""
    import llm_backends
    out = {"backends": llm_backends.available_backends(), "env_file_loaded": bool(os.getenv("ANTHROPIC_API_KEY")
           or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))}
    if test:
        try:
            out["test"] = llm_backends.check_llm(llm_backends.auto_llm())
        except llm_backends.LLMError as e:
            out["test"] = {"ok": False, "error": str(e)}
    return out


@app.get("/api/llm/models")
def llm_models(backend: str):
    """Models available for a backend: live list for Ollama (installed) and Groq, defaults for the others."""
    import llm_backends
    try:
        return {"backend": backend, "models": llm_backends.list_models(backend)}
    except llm_backends.LLMError as e:
        return {"backend": backend, "models": [], "error": str(e)}


@app.get("/api/scenarios")
def scenarios():
    return [{"name": n, "calls_per_min": sc.calls_per_min} for n, sc in SCENARIOS.items()]


# --------------------------------------------------------------------------
# Live call: you play the advisor, the simulator plays the client
# --------------------------------------------------------------------------
class StartCall(BaseModel):
    scenario: str = "normal"
    seed: Optional[int] = None
    wait_seconds: float = Field(0, ge=0, le=3600)
    llm: str = "auto"            # auto | none | claude | gemini | groq | ollama
    model: Optional[str] = None  # optional model name (see GET /api/llm/models)
    judge_llm: str = "none"      # LLM that judges the advisor's messages (none = keyword rules)
    language: str = "auto"       # auto | fr | darija | en
    difficulty: float = Field(0, ge=0, le=1)


class Reply(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@app.post("/api/calls")
def start_call(body: StartCall):
    if body.scenario not in SCENARIOS:
        raise HTTPException(400, f"Unknown scenario '{body.scenario}'")
    if body.language != "auto" and body.language not in LANGUAGES:
        raise HTTPException(400, f"Unknown language '{body.language}'")
    weights = None if body.language == "auto" else {body.language: 1.0}
    sim = ClientSimulatorAgent(seed=body.seed, llm=make_llm(body.llm, "client", body.model), judge_llm=make_llm(body.judge_llm, "judge"),
                               difficulty=body.difficulty, language_weights=weights)
    requests = sim.generate_requests(body.scenario, 10)
    if not requests:
        raise HTTPException(500, "No request generated, try again.")
    st = sim.start(sim.rng.choice(requests), wait_seconds=body.wait_seconds)
    call_id = uuid.uuid4().hex[:12]
    if len(CALLS) >= MAX_CALLS:
        CALLS.pop(next(iter(CALLS)))
    CALLS[call_id] = (sim, st)
    return view(call_id, sim, st)


@app.get("/api/calls/{call_id}")
def read_call(call_id: str):
    sim, st = get_call(call_id)
    return view(call_id, sim, st)


@app.post("/api/calls/{call_id}/reply")
def reply(call_id: str, body: Reply):
    sim, st = get_call(call_id)
    if st.status != "active":
        raise HTTPException(409, f"The call is over ({st.status}).")
    sim.reply(st, body.message.strip())
    return view(call_id, sim, st)


@app.post("/api/calls/{call_id}/end")
def end_call(call_id: str):
    sim, st = get_call(call_id)
    if st.status == "active":
        st.status = "closed"
    return view(call_id, sim, st)


# --------------------------------------------------------------------------
# Call-center simulation (SimPy): fifo vs priority
# --------------------------------------------------------------------------
class SimRequest(BaseModel):
    scenario: str = "normal"
    n_advisors: int = Field(3, ge=1, le=50)
    duration_min: float = Field(30, ge=1, le=240)
    seed: int = 7
    difficulty: float = Field(0, ge=0, le=1)
    start_hour: float = Field(8, ge=0, le=23)
    advisor: str = "baseline"    # baseline (verifies identity) | naive (never verifies: fraud leaks)
    save: bool = True            # store the run in the database when one is available


@app.post("/api/simulate")
def simulate(body: SimRequest):
    if body.scenario not in SCENARIOS:
        raise HTTPException(400, f"Unknown scenario '{body.scenario}'")
    try:
        from advisors import baseline_advisor, naive_advisor
        from sim_env import CallCenterEnv
    except ImportError:
        raise HTTPException(501, "SimPy is not installed. Run: pip install simpy")
    advisor_fn = {"baseline": baseline_advisor, "naive": naive_advisor}.get(body.advisor)
    if advisor_fn is None:
        raise HTTPException(400, f"Unknown advisor '{body.advisor}'")
    results = {}
    for policy in ("fifo", "priority"):
        env = CallCenterEnv(n_advisors=body.n_advisors, scenario=body.scenario,
                            duration_min=body.duration_min, seed=body.seed, policy=policy,
                            advisor_fn=advisor_fn, difficulty=body.difficulty, start_hour=body.start_hour,
                            store=STORE if body.save else None)
        res = env.run()
        results[policy] = {"kpis": env.kpis(), "statuses": dict(Counter(r["status"] for r in res)),
                           "run_id": env.run_id,
                           "incidents": [dict(name=i.name, start_s=i.start, end_s=i.end, multiplier=i.mult)
                                         for i in env.incidents]}
    return {**body.model_dump(), "results": results}


# --------------------------------------------------------------------------
# History stored in the database
# --------------------------------------------------------------------------
def need_store():
    if STORE is None:
        raise HTTPException(503, f"No database available: {DB_ERROR}")
    return STORE


@app.get("/api/runs")
def runs(limit: int = 20, kind: Optional[str] = None):
    return need_store().list_runs(limit, kind)


@app.get("/api/runs/{run_id}")
def run_detail(run_id: str):
    st = need_store()
    return {"run_id": run_id, "kpis": st.run_kpis(run_id), "incidents": st.run_incidents(run_id),
            "calls": st.run_calls(run_id)}


@app.get("/api/runs/{run_id}/calls/{call_id}")
def run_transcript(run_id: str, call_id: str):
    return need_store().transcript(run_id, call_id)


@app.get("/api/crm/{client_id}")
def crm_client(client_id: str):
    rec = need_store().get_client(client_id)
    if rec is None:
        raise HTTPException(404, "Unknown client")
    return rec


# --------------------------------------------------------------------------
# Voice: Whisper (speech to text) and gTTS (text to speech)
# --------------------------------------------------------------------------
class Speak(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    lang: str = "fr"
    slow: bool = False           # gTTS has no rate: this is the only prosody knob on the server voice


def _remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


@app.post("/api/audio/stt")
def speech_to_text_endpoint(file: UploadFile = File(...), language: str = "auto", model_size: str = "base"):
    try:
        from audio import speech_to_text
    except ImportError:
        raise HTTPException(501, "Audio support missing. Run: pip install openai-whisper")
    suffix = os.path.splitext(file.filename or "")[1] or ".webm"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file.file.read())
    try:
        return {"text": speech_to_text(tmp.name, model_size=model_size, language=language)}
    except ImportError:
        raise HTTPException(501, "Whisper is not installed. Run: pip install openai-whisper")
    except FileNotFoundError:
        raise HTTPException(500, "ffmpeg was not found. Install ffmpeg and restart the terminal.")
    except Exception as e:
        raise HTTPException(500, f"Transcription failed: {e}")
    finally:
        _remove(tmp.name)


@app.post("/api/audio/tts")
def text_to_speech_endpoint(body: Speak):
    try:
        from audio import text_to_speech
    except ImportError:
        raise HTTPException(501, "Audio support missing. Run: pip install gTTS")
    out = os.path.join(tempfile.gettempdir(), f"client_{uuid.uuid4().hex}.mp3")
    try:
        text_to_speech(body.text, out, body.lang, slow=body.slow)
    except ImportError:
        raise HTTPException(501, "gTTS is not installed. Run: pip install gTTS")
    except Exception as e:
        raise HTTPException(502, f"Speech synthesis failed (gTTS needs internet): {e}")
    return FileResponse(out, media_type="audio/mpeg", background=BackgroundTask(_remove, out))
