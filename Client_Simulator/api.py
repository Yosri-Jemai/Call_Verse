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
from profanity import LEVEL_NAME, scan as scan_profanity
from scorecard import score_call

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


def make_darija_llm(name: Optional[str], model: Optional[str] = None):
    """The dedicated Darija model. name: none | same | auto | derja | derja_hf | claude | gemini | groq | ollama.
    derja = ESPRIT-Derja-Qwen3-8B-v2 behind an OpenAI-compatible server; derja_hf = same model loaded locally.
    none / same = the main client LLM also writes the Darija lines. auto = DARIJA_LLM from .env,
    else the best configured backend (gemini > claude > groq > ollama); no key at all = main LLM."""
    import llm_backends
    if not name or name in ("none", "same"):
        return None
    key = ("darija", name, model)
    if key in _LLM_CACHE:
        return _LLM_CACHE[key]
    try:
        llm = llm_backends.darija_llm(None if name == "auto" else name, model)
    except Exception as e:
        if name in ("derja", "derja_hf", "auto"):
            # The ESPRIT-Derja server is not running (or no key): do not block the call, use the next
            # configured backend (groq > gemini > claude > ollama) and say why.
            for n in llm_backends.DARIJA_PREFERENCE:
                try:
                    fb = llm_backends.make_backend(n, "darija", None)
                except Exception:
                    continue
                _AUTO_REASON["darija"] = f"{name} unavailable ({e}); using {n} for Darija instead."
                return fb                       # not cached: the next call tries the preferred one again
            _AUTO_REASON["darija"] = str(e)
            return None                         # last resort: the main client LLM writes the Darija
        raise HTTPException(400, f"Cannot start the Darija '{name}' backend: {type(e).__name__}: {e}")
    _LLM_CACHE[key] = llm
    return llm


def make_toxicity_model(name: Optional[str]):
    """The optional ML bad-words model. A model that cannot start is an error, never a silent downgrade."""
    import toxicity_model
    try:
        return toxicity_model.load_toxicity_model(name)
    except toxicity_model.ToxicityModelError as e:
        raise HTTPException(400, str(e))


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
        # bad-words detection on the advisor's messages: totals + what the LAST message contained
        "moderation": moderation_view(st, sim.profanity_strikes if sim.profanity_hangup else 99),
        # which engine really wrote the client's lines (no more guessing why it looks rule-based)
        "engine": {**sim.engine_report(st),
                   "warning": _AUTO_REASON.get("client", "") if sim.llm is None else ""},
    }
    if st.status != "active":
        out["summary"] = sim.summary(st)
        out["scorecard"] = score_call(st)
        persist_call(call_id, sim, st)
    return out


def moderation_view(st: ConversationState, strikes: int = 2) -> dict:
    last = st.turn_log[-1]["flags"] if st.turn_log else {}
    lvl = last.get("grossier_niveau", 0)
    return {"count": st.profanity_count, "points": st.profanity_points, "ended": st.ended_by_profanity,
            "strikes_left": max(0, strikes - st.profanity_count), "warnings_max": (strikes - 1) if strikes < 99 else None,   # None: the call is never ended for bad words
           
            "client_swears": st.client_swears, "client_warnings": st.client_warnings,
            "client_abuse_ended": st.ended_by_client_abuse,
            "client_swore_last": bool(st.turn_log and st.turn_log[-1].get("client_swore")),
            "last": ({"level": lvl, "level_name": LEVEL_NAME[lvl], "words": last.get("grossier_mots", []),
                      "categories": last.get("grossier_categories", [])} if lvl else None)}


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
    return [{"name": n, "calls_per_min": sc.calls_per_min, "description": sc.description}
            for n, sc in SCENARIOS.items()]


# --------------------------------------------------------------------------
# Live call: you play the advisor, the simulator plays the client
# --------------------------------------------------------------------------
class StartCall(BaseModel):
    seed: Optional[int] = None
    scenario: str = "normal"
    wait_seconds: float = Field(0, ge=0, le=3600)
    mood: float = Field(0, ge=-1, le=1)
    max_turns: int = Field(8, ge=1, le=30)
    llm: str = "auto"            # auto | none | claude | gemini | groq | ollama
    model: Optional[str] = None  # optional model name (see GET /api/llm/models)
    judge_llm: str = "none"      # LLM that judges the advisor's messages (none = keyword rules)
    darija_llm: str = "auto"     # model that writes the Darija lines: auto | same | derja | derja_hf | claude | gemini | groq | ollama
    darija_model: Optional[str] = None   # optional model name for the Darija model
    language: str = "auto"       # auto | fr | darija | en
    difficulty: float = Field(0, ge=0, le=1)
    profanity_hangup: bool = True  # the client warns at the first offence, hangs up at the `profanity_strikes`-th
    profanity_strikes: int = Field(2, ge=1, le=5)
    client_swears: bool = True     # the angry client swears: lets you check the advisor's de-escalation
    client_swear_turn: Optional[int] = Field(None, ge=1)  # abusive client: swears from this advisor turn on
    client_swear_strikes: int = Field(3, ge=1, le=5)      # the client's N-th outburst ends the call
    toxicity_model: str = "none"   # ML bad-words model on top of the word list: none | auto | <Hugging Face model name>


class Reply(BaseModel):
    message: str = Field(min_length=1, max_length=2000)


@app.post("/api/calls")
def start_call(body: StartCall):
    if body.scenario not in SCENARIOS:
        raise HTTPException(400, f"Unknown scenario '{body.scenario}'")
    if body.language != "auto" and body.language not in LANGUAGES:
        raise HTTPException(400, f"Unknown language '{body.language}'")
    weights = None if body.language == "auto" else {body.language: 1.0}
    main_llm = make_llm(body.llm, "client", body.model)
    # dedicated Darija model, only worth loading when a LLM plays the client and Darija can occur
    explicit_darija = body.darija_llm not in ("auto", "same", "none", "")
    dar_llm = (make_darija_llm(body.darija_llm, body.darija_model)
               if body.language in ("auto", "darija") and (main_llm is not None or explicit_darija) else None)
    sim = ClientSimulatorAgent(seed=body.seed, llm=main_llm, judge_llm=make_llm(body.judge_llm, "judge"),
                               max_turns=body.max_turns, difficulty=body.difficulty,
                               language_weights=weights, darija_llm=dar_llm)
    requests = sim.generate_requests(body.scenario, 10)
    if not requests:
        raise HTTPException(500, "No request generated, try again.")
    request = sim.rng.choice(requests)
    request.initial_mood = body.mood
    st = sim.start(request, wait_seconds=body.wait_seconds)
    call_id = uuid.uuid4().hex[:12]
    if len(CALLS) >= MAX_CALLS:
        CALLS.pop(next(iter(CALLS)))
    CALLS[call_id] = (sim, st)
    return view(call_id, sim, st)


@app.get("/api/calls/{call_id}")
def read_call(call_id: str):
    sim, st = get_call(call_id)
    return view(call_id, sim, st)


@app.get("/api/calls/{call_id}/scorecard")
def call_scorecard(call_id: str):
    """QA scorecard of a call (works while it is running too: criteria that do not apply yet are `na`)."""
    _, st = get_call(call_id)
    return score_call(st)


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
# Bad-words detection (the same detector the judge uses on every advisor message)
# --------------------------------------------------------------------------
class ModerationCheck(BaseModel):
    text: str = Field(max_length=2000)
    toxicity_model: str = "none"           # none | auto | <Hugging Face model name>


@app.post("/api/moderation/check")
def moderation_check(body: ModerationCheck):
    """Check a text for bad words (French, English, Darija). Bad words come back masked ("c*****d")."""
    r = scan_profanity(body.text)
    out = {"found": r["found"], "level": r["level"], "level_name": LEVEL_NAME[r["level"]],
           "count": r["count"], "words": r["words"], "categories": r["categories"], "masked": r["masked"]}
    model = make_toxicity_model(body.toxicity_model)
    if model is not None:
        m = model.predict(body.text)
        out["model"] = m                                   # {"score", "toxic", "model"} or None if it failed
        if m and m["toxic"] and not out["found"]:
            out.update(found=True, level=2 if m["score"] >= 0.95 else 1, categories=["model"])
            out["level_name"] = LEVEL_NAME[out["level"]]
    return out


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
    slow: bool = False           # gTTS fallback has no rate: this is its only prosody knob
    name: str = ""               # client name: picks a male / female voice
    gender: str = ""             # "f" | "m" (overrides the guess from the name)
    rate: float = Field(default=1.0, ge=0.6, le=1.6)
    pitch: float = Field(default=1.0, ge=0.8, le=1.25)


def _remove(path: str):
    try:
        os.remove(path)
    except OSError:
        pass


@app.get("/api/audio/status")
def audio_status():
    """Which voice engines are active (shown in the UI)."""
    from audio import stt_backend, tts_backend
    return {"stt": stt_backend(), "tts": tts_backend()}


@app.post("/api/audio/stt")
def speech_to_text_endpoint(file: UploadFile = File(...), language: str = "auto", model_size: str = "base"):
    try:
        from audio import speech_to_text
    except ImportError:
        raise HTTPException(501, "Audio support missing. Set GROQ_API_KEY or run: pip install openai-whisper")
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
        import traceback
        traceback.print_exc()                      # full error in the backend terminal
        raise HTTPException(500, f"Transcription failed: {type(e).__name__}: {e}")
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
        from audio import guess_gender
        gender = body.gender if body.gender in ("f", "m") else guess_gender(body.name)
        text_to_speech(body.text, out, body.lang, slow=body.slow, gender=gender, rate=body.rate, pitch=body.pitch)
    except ImportError:
        raise HTTPException(501, "gTTS is not installed. Run: pip install gTTS")
    except Exception as e:
        raise HTTPException(502, f"Speech synthesis failed (gTTS needs internet): {e}")
    return FileResponse(out, media_type="audio/mpeg", background=BackgroundTask(_remove, out))
