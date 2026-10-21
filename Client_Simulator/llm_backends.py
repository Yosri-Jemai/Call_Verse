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
    cfg = dict(max_output_tokens=max_tokens, temperature=temperature)
    try:   # 2.5 models spend output tokens on "thinking": that truncated the JSON answers
        cfg["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
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


FACTORIES = {"claude": claude_llm, "gemini": gemini_llm, "groq": groq_llm, "ollama": ollama_llm}


def make_backend(name: str, role: str = "client", model: str | None = None):
    """Build one backend by name. role='judge' uses a cheaper model and temperature 0.
    `model` overrides the default (e.g. a model listed by list_models('ollama'))."""
    if name not in FACTORIES:
        raise LLMError(f"Unknown LLM backend '{name}' (choose: {', '.join(FACTORIES)})")
    kw = {}
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