"""
LLM backends for the Client Simulator. Each factory returns a function
    llm(prompt: str) -> str
that you pass to ClientSimulatorAgent(llm=...). The function also carries two attributes,
`.backend` and `.model`, so the UI / logs can say which engine is really speaking.

What changed (these were the reasons the client always fell back to rules):
  * .env is now loaded automatically (before, ANTHROPIC_API_KEY in .env was never read).
  * max_tokens was 120: the client answers with JSON (reply + emotion + mood_adjust + hang_up),
    which was cut in the middle -> invalid JSON -> silent fallback. Default is now 500.
  * Gemini 2.5 "thinking" tokens count against max_output_tokens: thinking is switched off.
  * auto_llm() picks the first backend that is really configured, and says why when none is.
  * check_llm() does a real round trip, so a bad key / model / network is visible at once.
"""
from __future__ import annotations

import os
from pathlib import Path


class LLMError(RuntimeError):
    """The LLM backend is missing, misconfigured or unreachable (message says what to fix)."""


# --------------------------------------------------------------------------
# .env loading (no dependency). Real environment variables always win.
# --------------------------------------------------------------------------
def load_env(path: str | os.PathLike | None = None) -> list[str]:
    """Read KEY=VALUE lines from a .env file into os.environ. Returns the keys that were set."""
    candidates = [Path(path)] if path else [Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"]
    loaded: list[str] = []
    for p in candidates:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip().removeprefix("export ").strip(), val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
                val = val[1:-1]
            elif " #" in val:
                val = val.split(" #", 1)[0].strip()
            if key and val and key not in os.environ:
                os.environ[key] = val
                loaded.append(key)
    return loaded


load_env()

DEFAULT_MODELS = {"claude": "claude-sonnet-5-5", "gemini": "gemini-2.5-flash",
                  "groq": "llama-3.3-70b-versatile", "ollama": "mistral"}
# Dedicated "darija" role: the model that writes ONLY the Tunisian Arabizi lines. Pick it with
# DARIJA_LLM=gemini|claude|groq|ollama and DARIJA_MODEL=<model name> in .env (both optional).
# Tunisian Darija is poorly covered by small models (llama 3B / mistral answer in French or MSA),
# so the preference order is the models that handle Maghrebi Arabic best.
DARIJA_PREFERENCE = ("gemini", "claude", "groq", "ollama")   # "derja" goes first when DERJA_BASE_URL is set
DARIJA_MODELS = {"claude": "claude-sonnet-5-5", "gemini": "gemini-2.5-pro",
                 "groq": None, "ollama": None}          # None = best one that is really available
JUDGE_MODELS = {"claude": "claude-haiku-5-5", "gemini": "gemini-2.5-flash",
                "groq": "llama-3.1-8b-instant", "ollama": "mistral"}


def _http_json(url: str, payload: dict | None = None, headers: dict | None = None, timeout: float = 60.0):
    """Tiny JSON-over-HTTP helper (stdlib only), used by Groq and Ollama: no extra package needed."""
    import json
    import urllib.error
    import urllib.request
    h = {"Content-Type": "application/json", "User-Agent": "client-simulator/1.0", **(headers or {})}
    req = urllib.request.Request(url, data=json.dumps(payload).encode() if payload is not None else None,
                                 headers=h, method="POST" if payload is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError(f"HTTP {e.code} from {url}: {e.read().decode('utf-8', 'replace')[:300]}") from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise LLMError(f"Cannot reach {url}: {getattr(e, 'reason', e)}") from e


def _tag(fn, backend: str, model: str):
    fn.backend, fn.model = backend, model
    return fn


def claude_llm(model: str = DEFAULT_MODELS["claude"], max_tokens: int = 500, temperature: float = 0.8,
               timeout: float = 30.0):
    """Claude API. Needs `pip install anthropic` and ANTHROPIC_API_KEY (env or .env)."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise LLMError("ANTHROPIC_API_KEY is not set (put it in .env or the environment).")
    try:
        from anthropic import Anthropic
    except ImportError as e:
        raise LLMError("The 'anthropic' package is missing: pip install anthropic") from e
    client = Anthropic(timeout=timeout, max_retries=2)

    def call(prompt: str) -> str:
        r = client.messages.create(
            model=model, max_tokens=max_tokens, temperature=temperature,
            messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in r.content if getattr(b, "type", "") == "text").strip()
        if not text:
            raise LLMError(f"Claude returned no text (stop_reason={r.stop_reason})")
        return text
    return _tag(call, "claude", model)


def _ollama_host() -> str:
    h = os.getenv("OLLAMA_HOST", "http://localhost:11434").strip().rstrip("/")
    return h if h.startswith("http") else "http://" + h


def ollama_models() -> list[str]:
    """Models installed in the local Ollama (`ollama list`). Raises LLMError if the server is down."""
    try:
        data = _http_json(_ollama_host() + "/api/tags", timeout=3)
    except LLMError as e:
        raise LLMError(f"Ollama is not running at {_ollama_host()} (start it with `ollama serve`): {e}") from e
    skip = ("embed", "bge-", "minilm", "bert", "rerank")         # embedding models cannot chat
    return [m["name"] for m in data.get("models", []) if not any(x in m["name"].lower() for x in skip)]


def ollama_llm(model: str | None = None, temperature: float = 0.8, json_mode: bool = True,
               timeout: float = 120.0):
    """Local model through the Ollama HTTP API (no pip package needed). Ollama must be running.
    If `model` is not installed, the first installed model is used; none installed -> clear error."""
    installed = ollama_models()
    if not installed:
        raise LLMError("Ollama is running but has no model. Install one: `ollama pull llama3.2` (or mistral).")
    wanted = model or DEFAULT_MODELS["ollama"]
    names = {n: n for n in installed} | {n.split(":")[0]: n for n in installed}
    chosen = names.get(wanted)
    if chosen is None:
        if model:       # the user asked for a specific model: do not silently swap it
            raise LLMError(f"Ollama model '{model}' is not installed. Installed: {', '.join(installed)}")
        chosen = installed[0]

    def call(prompt: str) -> str:
        payload = {"model": chosen, "messages": [{"role": "user", "content": prompt}], "stream": False,
                   "options": {"temperature": temperature}}
        if json_mode:
            payload["format"] = "json"
        r = _http_json(_ollama_host() + "/api/chat", payload, timeout=timeout)
        text = (r.get("message", {}).get("content") or "").strip()
        if not text:
            raise LLMError(f"Ollama model '{chosen}' returned no text")
        return text
    return _tag(call, "ollama", chosen)


# Which Groq models exist depends on the account and changes over time: never assume one is there.
GROQ_PREFERENCE = ["llama-3.3-70b-versatile", "openai/gpt-oss-120b", "qwen/qwen3-32b", "openai/gpt-oss-20b",
                   "llama-3.1-8b-instant"]
GROQ_JUDGE_PREFERENCE = ["llama-3.1-8b-instant", "openai/gpt-oss-20b", "llama-3.3-70b-versatile",
                         "openai/gpt-oss-120b"]


def _rank(ids: list[str], preference: list[str]) -> list[str]:
    """Preferred models first (in preference order), then the others alphabetically."""
    first = [p for p in preference if p in ids]
    return first + [i for i in ids if i not in first]


def groq_models() -> list[str]:
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise LLMError("GROQ_API_KEY is not set (put it in .env or the environment).")
    data = _http_json("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=10)
    skip = ("whisper", "tts", "guard", "playai", "orpheus", "embed")      # audio / safety models cannot chat
    ids = sorted(m["id"] for m in data.get("data", []) if not any(x in m["id"] for x in skip))
    return _rank(ids, GROQ_PREFERENCE)


def groq_llm(model: str | None = None, max_tokens: int = 500, temperature: float = 0.8,
             timeout: float = 30.0, json_mode: bool = True, judge: bool = False):
    """Groq API (OpenAI-compatible). Needs GROQ_API_KEY (env or .env). No pip package needed.
    model=None picks the best model YOUR account really has (see GROQ_PREFERENCE)."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        raise LLMError("GROQ_API_KEY is not set (put it in .env or the environment).")
    if model is None:
        try:
            ids = groq_models()
        except LLMError:
            ids = []
        if not ids:
            model = DEFAULT_MODELS["groq"]            # cannot list: try the default and let the API answer
        else:
            model = _rank(ids, GROQ_JUDGE_PREFERENCE if judge else GROQ_PREFERENCE)[0]
    state = {"json": json_mode}

    def call(prompt: str) -> str:
        reasoning = any(x in model for x in ("gpt-oss", "qwen3", "deepseek-r1"))
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}],
                   # reasoning models spend tokens thinking before the answer: give them room
                   "max_tokens": max(max_tokens, 2000) if reasoning else max_tokens, "temperature": temperature}
        if state["json"] and "json" in prompt.lower():        # Groq requires the word JSON in the prompt
            payload["response_format"] = {"type": "json_object"}
        try:
            r = _http_json("https://api.groq.com/openai/v1/chat/completions", payload,
                           {"Authorization": f"Bearer {key}"}, timeout=timeout)
        except LLMError as e:
            if "response_format" in payload and "response_format" in str(e):
                state["json"] = False                         # this model has no JSON mode: ask without it
                payload.pop("response_format")
                r = _http_json("https://api.groq.com/openai/v1/chat/completions", payload,
                               {"Authorization": f"Bearer {key}"}, timeout=timeout)
            else:
                raise
        try:
            text = (r["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"Unexpected Groq answer: {str(r)[:200]}")
        import re
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        if not text:
            raise LLMError(f"Groq model '{model}' returned no text (finish_reason="
                           f"{r['choices'][0].get('finish_reason')}): try another model")
        return text
    return _tag(call, "groq", model)


def list_models(backend: str) -> list[str]:
    """Models the user can pick for a backend (live list for ollama / groq, defaults otherwise)."""
    if backend == "ollama":
        return ollama_models()
    if backend == "groq":
        return groq_models()
    if backend in DEFAULT_MODELS:
        return [DEFAULT_MODELS[backend]] if backend != "claude" else ["claude-sonnet-5-5", "claude-haiku-5-5"]
    raise LLMError(f"Unknown LLM backend '{backend}'")


def gemini_llm(model: str = DEFAULT_MODELS["gemini"], max_tokens: int = 500, temperature: float = 0.8):
    """Gemini API. Needs `pip install google-genai` and GEMINI_API_KEY / GOOGLE_API_KEY (env or .env)."""
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        raise LLMError("GEMINI_API_KEY is not set (put it in .env or the environment).")
    try:
        from google import genai
        from google.genai import types
    except ImportError as e:
        raise LLMError("The 'google-genai' package is missing: pip install google-genai") from e
    client = genai.Client()
    is_pro = "pro" in model
    if is_pro:
        max_tokens = max(max_tokens, 1500)     # Pro always thinks: leave room for the visible answer
    cfg = dict(max_output_tokens=max_tokens, temperature=temperature)
    try:   # 2.5 models spend output tokens on "thinking": that truncated the JSON answers
        # Flash can switch thinking off (0). Pro cannot: its minimum budget is 128.
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=128 if is_pro else 0)
    except Exception:
        pass

    def call(prompt: str) -> str:
        r = client.models.generate_content(model=model, contents=prompt,
                                           config=types.GenerateContentConfig(**cfg))
        text = (r.text or "").strip()
        if not text:
            raise LLMError("Gemini returned no text (blocked or empty answer)")
        return text
    return _tag(call, "gemini", model)


# --------------------------------------------------------------------------
# ESPRIT-Derja-Qwen3-8B-v2: Tunisian Darija (Arabic + Arabizi) model, used ONLY for Darija lines.
# https://huggingface.co/ESPRIT-Group/ESPRIT-Derja-Qwen3-8B-v2   (Apache 2.0, LoRA on Qwen3-8B)
# It is a conversation model: it does not follow long JSON prompts, so the simulator gives it a
# short prompt (prompt_style = "derja") and takes a plain-text answer.
# Two ways to run it:
#   derja     : OpenAI-compatible server (vLLM, Docker Model Runner, ESPRIT AI Forge...), see DERJA_* env vars
#   derja_hf  : load it in this process with `transformers` (needs a GPU with ~16 GB, or DERJA_4BIT=1)
# --------------------------------------------------------------------------
DERJA_MODEL_ID = "ESPRIT-Group/ESPRIT-Derja-Qwen3-8B-v2"
DERJA_SERVED_NAME = "ESPRIT-Derja-Qwen3-8B-v2"
DERJA_SYSTEM = (
    "Inti ESPRIT-Derja, modele IA ta3 ESPRIT Tunisie. Toujours jaweb bel dialecte tounsi (darija tunisienne).\n"
    "Ista3mel l-arabizi (3=ع, 7=ح, 9=ق, 5=خ, 8=غ) walla l-3arbiya tounsiya 7asb ki yekteblek el user.\n"
    "Jaweb b tari9a tabi3iya, kima tounsi ya7ki m3a sa7bou."
)


def _derja_clean(text: str) -> str:
    """Remove what Qwen3 / chat models add around the answer: <think> blocks, role labels, quotes."""
    import re
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</?think>", "", text, flags=re.IGNORECASE).strip()
    text = re.sub(r"^(?:MOI(?: \(le client\))?|CLIENT|Client|Assistant|ESPRIT-Derja)\s*:\s*", "", text).strip()
    return text.strip().strip('"\u00ab\u00bb').strip()


def _derja_base_url() -> str:
    return os.getenv("DERJA_BASE_URL", "http://localhost:8009/v1").strip().rstrip("/")


def derja_llm(model: str | None = None, max_tokens: int = 200, temperature: float = 0.7,
              timeout: float = 90.0):
    """ESPRIT-Derja-Qwen3-8B-v2 behind an OpenAI-compatible endpoint.
    .env: DERJA_BASE_URL (default http://localhost:8009/v1), DERJA_API_KEY (optional),
    DERJA_SERVED_MODEL (default ESPRIT-Derja-Qwen3-8B-v2 = the --served-model-name of vLLM)."""
    base = _derja_base_url()
    served = model or os.getenv("DERJA_SERVED_MODEL") or DERJA_SERVED_NAME
    headers = {"Authorization": f"Bearer {os.getenv('DERJA_API_KEY')}"} if os.getenv("DERJA_API_KEY") else {}
    try:                                    # fail fast with a clear message if the server is not up
        data = _http_json(base + "/models", headers=headers, timeout=4)
    except LLMError as e:
        raise LLMError(f"ESPRIT-Derja server not reachable at {base}. Start it (see README: 'Darija model') "
                       f"or set DERJA_BASE_URL. Details: {e}") from e
    ids = [m.get("id") for m in (data.get("data") or []) if isinstance(m, dict)]
    if ids and served not in ids:
        if model:
            raise LLMError(f"Model '{served}' is not served at {base}. Served: {', '.join(map(str, ids))}")
        served = ids[0]                     # one model served: use it, whatever name it was given
    state = {"template_kwargs": True}

    def call(prompt: str) -> str:
        payload = {"model": served, "temperature": temperature, "max_tokens": max_tokens,
                   "messages": [{"role": "system", "content": DERJA_SYSTEM},
                                {"role": "user", "content": prompt}]}
        if state["template_kwargs"]:        # Qwen3: no "thinking" block, only the answer
            payload["chat_template_kwargs"] = {"enable_thinking": False}
        try:
            r = _http_json(base + "/chat/completions", payload, headers, timeout=timeout)
        except LLMError as e:
            if state["template_kwargs"] and "chat_template_kwargs" in str(e):
                state["template_kwargs"] = False   # this server rejects the extra field: retry without
                payload.pop("chat_template_kwargs")
                r = _http_json(base + "/chat/completions", payload, headers, timeout=timeout)
            else:
                raise
        try:
            text = _derja_clean(r["choices"][0]["message"]["content"] or "")
        except (KeyError, IndexError, TypeError):
            raise LLMError(f"Unexpected answer from the Derja server: {str(r)[:200]}")
        if not text:
            raise LLMError("ESPRIT-Derja returned no text")
        return text
    fn = _tag(call, "esprit-derja", served)
    fn.prompt_style = "derja"
    return fn


_HF_CACHE: dict = {}


def derja_hf_llm(model: str | None = None, max_tokens: int = 200, temperature: float = 0.7):
    """ESPRIT-Derja-Qwen3-8B-v2 loaded locally with `transformers` (first call downloads ~16 GB).
    pip install transformers torch accelerate   (+ bitsandbytes and DERJA_4BIT=1 for a 6 GB GPU)."""
    import threading
    model_id = model or os.getenv("DERJA_HF_MODEL") or DERJA_MODEL_ID
    try:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as e:
        raise LLMError("Local Derja needs: pip install transformers torch accelerate") from e
    if model_id not in _HF_CACHE:
        kw = {"device_map": "auto"}
        if os.getenv("DERJA_4BIT") == "1":
            try:
                from transformers import BitsAndBytesConfig
                kw["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16)
            except ImportError as e:
                raise LLMError("DERJA_4BIT=1 needs: pip install bitsandbytes") from e
        else:
            kw["torch_dtype"] = torch.bfloat16
        try:
            tok = AutoTokenizer.from_pretrained(model_id)
            mdl = AutoModelForCausalLM.from_pretrained(model_id, **kw)
        except Exception as e:
            raise LLMError(f"Cannot load {model_id}: {type(e).__name__}: {e}") from e
        _HF_CACHE[model_id] = (tok, mdl, threading.Lock())
    tok, mdl, lock = _HF_CACHE[model_id]

    def call(prompt: str) -> str:
        msgs = [{"role": "system", "content": DERJA_SYSTEM}, {"role": "user", "content": prompt}]
        try:
            text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        except TypeError:
            text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
        inputs = tok(text, return_tensors="pt").to(mdl.device)
        with lock:                           # one generation at a time (API requests are threaded)
            out = mdl.generate(**inputs, max_new_tokens=max_tokens, temperature=temperature, do_sample=True)
        answer = _derja_clean(tok.decode(out[0][inputs["input_ids"].shape[-1]:], skip_special_tokens=True))
        if not answer:
            raise LLMError("ESPRIT-Derja (local) returned no text")
        return answer
    fn = _tag(call, "esprit-derja-local", model_id)
    fn.prompt_style = "derja"
    return fn


FACTORIES = {"claude": claude_llm, "gemini": gemini_llm, "groq": groq_llm, "ollama": ollama_llm}


# Backends that only make sense for Darija lines: not offered as the main client model, not auto-picked.
DARIJA_ONLY = {"derja": derja_llm, "derja_hf": derja_hf_llm}


def make_backend(name: str, role: str = "client", model: str | None = None):
    """Build one backend by name. role='judge' uses a cheaper model and temperature 0.
    `model` overrides the default (e.g. a model listed by list_models('ollama'))."""
    if name in DARIJA_ONLY:
        return DARIJA_ONLY[name](model=model) if model else DARIJA_ONLY[name]()
    if name not in FACTORIES:
        raise LLMError(f"Unknown LLM backend '{name}' (choose: {', '.join(list(FACTORIES) + list(DARIJA_ONLY))})")
    kw = {}
    if role == "darija":
        kw = {"temperature": 0.9}
        if name != "ollama":
            kw["max_tokens"] = 600
        if DARIJA_MODELS.get(name):
            kw["model"] = DARIJA_MODELS[name]
        if name == "groq":
            kw["model"] = None
    if role == "judge":
        kw = {"model": JUDGE_MODELS[name], "temperature": 0.0}
        if name != "ollama":
            kw["max_tokens"] = 500
        if name in ("ollama", "groq"):
            kw["model"] = None          # use what is really installed / available on this account
        if name == "groq":
            kw["judge"] = True
    if model:
        kw["model"] = model
    return FACTORIES[name](**kw)


def available_backends() -> dict:
    """{backend: 'ready' | reason it cannot be used}. Cheap: no network call."""
    out = {}
    for name in FACTORIES:
        try:
            make_backend(name)
            out[name] = "ready"
        except LLMError as e:
            out[name] = str(e)
        except Exception as e:
            out[name] = f"{type(e).__name__}: {e}"
    try:                                     # Darija-only model: cheap server ping, no 16 GB download
        derja_llm()
        out["derja"] = "ready (ESPRIT-Derja-Qwen3-8B-v2, Darija only)"
    except LLMError as e:
        out["derja"] = str(e)
    return out


def auto_llm(role: str = "client", prefer: tuple = ("claude", "gemini", "groq", "ollama")):
    """First configured backend. Raises LLMError listing why each one was skipped."""
    why = []
    for name in prefer:
        try:
            return make_backend(name, role)
        except Exception as e:
            why.append(f"{name}: {e}")
    raise LLMError("No LLM backend available -> " + " | ".join(why))


def darija_llm(name: str | None = None, model: str | None = None):
    """The model dedicated to Tunisian Darija. Order of precedence:
    explicit `name` > DARIJA_LLM env > first ready backend in DARIJA_PREFERENCE.
    DARIJA_MODEL (env) overrides the model name. Raises LLMError listing why each was skipped."""
    name = name or os.getenv("DARIJA_LLM") or None
    model = model or os.getenv("DARIJA_MODEL") or None
    if name and name not in ("auto", "same"):
        return make_backend(name, "darija", model)
    why = []
    order = (("derja",) if os.getenv("DERJA_BASE_URL") else ()) + DARIJA_PREFERENCE
    for n in order:
        try:
            return make_backend(n, "darija", model if name is None and os.getenv("DARIJA_LLM") else None)
        except Exception as e:
            why.append(f"{n}: {e}")
    raise LLMError("No Darija LLM available -> " + " | ".join(why))


def check_llm(llm) -> dict:
    """Round trip with a tiny JSON request: {ok, backend, model, error, sample}."""
    info = {"ok": False, "backend": getattr(llm, "backend", "custom"), "model": getattr(llm, "model", "?"),
            "error": "", "sample": ""}
    try:
        raw = llm('Réponds uniquement par cet objet JSON : {"reply": "ok"}')
        info["sample"] = raw[:120]
        info["ok"] = bool((extract_json(raw) or {}).get("reply"))
        if not info["ok"]:
            info["error"] = "The model answered, but not with valid JSON."
    except Exception as e:
        info["error"] = f"{type(e).__name__}: {e}"
    return info


# --------------------------------------------------------------------------
# Helper shared by the LLM client and the LLM judge
# --------------------------------------------------------------------------
def extract_json(text: str):
    """Pull the first JSON object out of an LLM answer.

    Models often wrap JSON in ```json fences or add a sentence around it. Returns the
    parsed dict, or None if no valid JSON object is found (the caller then falls back).
    """
    import json
    import re

    if not isinstance(text, str):
        return None
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    # models sometimes copy the "// comment" of the prompt template, which breaks json.loads
    text = re.sub(r'("(?:[^"\\]|\\.)*")|//[^\n]*', lambda m: m.group(1) or "", text)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        pass
    start = text.find("{")
    while start != -1:                      # try each "{" until a balanced, valid object parses
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            c = text[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except ValueError:
                        pass
                    break
        start = text.find("{", start + 1)
    return None


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "check":
        print("backends:", available_backends())
        try:
            llm = auto_llm()
            print("round trip:", check_llm(llm))
        except LLMError as e:
            print("NO LLM:", e)
    else:
        print("usage: python llm_backends.py check")