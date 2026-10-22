"""
Optional audio channel: advisor voice -> text (Whisper), client text -> voice (gTTS).
Needs: pip install edge-tts gTTS   (Groq key for fast STT; or openai-whisper + ffmpeg for local STT)
Imports are lazy so the rest of the project works without these libraries.
"""
from __future__ import annotations

from functools import lru_cache

from client_simulator import ClientSimulatorAgent, ConversationState


@lru_cache(maxsize=2)
def _whisper_model(size: str):
    """Load each local Whisper model once (loading is slow)."""
    import whisper
    return whisper.load_model(size)


# Banking vocabulary given to Whisper as a prompt: it strongly improves the spelling of domain words.
STT_PROMPT = ("Conversation téléphonique avec un conseiller bancaire : carte bancaire, virement, plafond, "
              "opposition, IBAN, RIB, relevé, prélèvement, solde, découvert, crédit, agence, application mobile, "
              "code secret, date de naissance, réclamation, fraude, BIAT, STB, Amen Bank.")


def _load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass


def _groq_transcribe(audio_path: str, language: str | None) -> str:
    """Groq-hosted Whisper large-v3 (fast, much better than local 'base'). Uses only the stdlib."""
    import json
    import mimetypes
    import os
    import urllib.error
    import urllib.request
    import uuid

    _load_env()
    key = os.environ["GROQ_API_KEY"]
    model = os.environ.get("GROQ_STT_MODEL", "whisper-large-v3")
    fields = {"model": model, "response_format": "json", "temperature": "0", "prompt": STT_PROMPT}
    if language not in (None, "", "auto"):
        fields["language"] = language
    boundary = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    fname = os.path.basename(audio_path)
    ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
    with open(audio_path, "rb") as fh:
        data = fh.read()
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{fname}"\r\n'
                 f'Content-Type: {ctype}\r\n\r\n'.encode() + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/audio/transcriptions", data=b"".join(parts), method="POST",
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json",
                 "User-Agent": "client-simulator/1.0",       # Python-urllib's default UA is blocked (HTTP 403, Cloudflare)
                 "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())["text"].strip()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Groq STT HTTP {e.code}: {e.read().decode(errors='ignore')[:200]}")


# Whisper invents these sentences on silence / noise: they must never reach the client.
_HALLUCINATIONS = ("sous-titres", "merci d'avoir regardé", "thanks for watching", "amara.org",
                   "sous-titrage", "subtitles by", "abonnez-vous")


def is_hallucination(text: str) -> bool:
    t = text.strip().lower()
    return len(t) < 2 or any(h in t for h in _HALLUCINATIONS)


def stt_backend() -> str:
    import os
    _load_env()
    return "groq" if os.environ.get("GROQ_API_KEY") else "whisper-local"


def speech_to_text(audio_path: str, model_size: str = "base", language: str | None = None) -> str:
    """Groq Whisper large-v3 when GROQ_API_KEY is set, else local Whisper.
    language=None / "auto" lets Whisper detect it. Heavy Darija is still the weak spot of Whisper."""
    if stt_backend() == "groq":
        try:
            text = _groq_transcribe(audio_path, language)
            return "" if is_hallucination(text) else text
        except Exception as e:
            # Do NOT silently download a 460 MB local model (hours on a slow line): show the real Groq error.
            # Set ALLOW_LOCAL_STT=1 in .env if you really want the local Whisper as a fallback.
            import os
            if os.environ.get("ALLOW_LOCAL_STT") != "1":
                raise RuntimeError(f"Groq speech-to-text failed: {e}")
    import os
    if os.environ.get("ALLOW_LOCAL_STT") != "1":
        # Never start a huge model download by surprise (that is what froze the call).
        raise RuntimeError("No GROQ_API_KEY found, so speech-to-text is off. Put GROQ_API_KEY=... in the .env file "
                           "next to api.py and restart (or set ALLOW_LOCAL_STT=1 to use local Whisper).")
    model = _whisper_model(model_size)
    opts = {"initial_prompt": STT_PROMPT}
    if language not in (None, "", "auto"):
        opts["language"] = language
    text = model.transcribe(audio_path, fp16=False, **opts)["text"].strip()
    return "" if is_hallucination(text) else text


# Tunisian Arabizi (Latin letters) -> Arabic script, used ONLY for text-to-speech.
# The client WRITES in Latin (the advisor must keep reading Latin), but a Latin string voiced by an
# Arabic voice (or gTTS "ar") comes out as gibberish; transliterating first makes the voice speak real
# Arabic. This is a best-effort phonetic mapping, not a full transliteration engine.
_ARABIZI_DIGRAPHS = [("ch", "ش"), ("kh", "خ"), ("gh", "غ"), ("sh", "ش"), ("th", "ث"), ("dh", "ذ"),
                     ("ou", "و"), ("oo", "و"), ("ai", "اي"), ("ei", "اي"), ("ph", "ف")]
_ARABIZI_CHARS = {"2": "ء", "3": "ع", "5": "خ", "6": "ط", "7": "ح", "8": "غ", "9": "ق",
                  "a": "ا", "b": "ب", "c": "ك", "d": "د", "e": "ي", "f": "ف", "g": "ڨ", "h": "ه",
                  "i": "ي", "j": "ج", "k": "ك", "l": "ل", "m": "م", "n": "ن", "o": "و", "p": "ب",
                  "q": "ق", "r": "ر", "s": "س", "t": "ت", "u": "و", "v": "ف", "w": "و", "x": "كس",
                  "y": "ي", "z": "ز"}


def arabizi_to_arabic(text: str) -> str:
    """Best-effort Arabizi -> Arabic script. Text already containing Arabic is returned unchanged."""
    if any("\u0600" <= ch <= "\u06ff" for ch in text):
        return text
    out, i, low = [], 0, text.lower()
    while i < len(text):
        if i + 1 < len(text):
            pair = low[i:i + 2]
            hit = next((ar for dg, ar in _ARABIZI_DIGRAPHS if dg == pair), None)
            if hit:
                out.append(hit)
                i += 2
                continue
        out.append(_ARABIZI_CHARS.get(low[i], text[i]))
        i += 1
    return "".join(out)


# Darija is full of French words (carte, virement, opération...). An Arabic voice reading them letter by
# letter is unintelligible, so a Darija sentence is cut into French / Darija pieces: French pieces are
# spoken by the French voice, Darija pieces by the Arabic voice (after Arabizi -> Arabic script).
_FRENCH_WORDS = {
    "carte", "cartes", "compte", "comptes", "virement", "virements", "solde", "frais", "crédit", "credit",
    "prélèvement", "prelevement", "plafond", "application", "agence", "conseiller", "conseillère", "dossier",
    "réclamation", "reclamation", "opposition", "code", "relevé", "releve", "mouvements", "mouvement",
    "opération", "operation", "opérations", "operations", "banque", "chèque", "cheque", "paiement", "retrait",
    "distributeur", "bloquée", "bloquee", "bloqué", "bloque", "secret", "mot", "passe", "téléphone",
    "telephone", "numéro", "numero", "date", "naissance", "chiffres", "derniers", "responsable", "service",
    "problème", "probleme", "fraude", "sécurité", "securite", "internet", "mobile", "ticket", "attente",
    "bonjour", "bonsoir", "merci", "oui", "non", "voilà", "voila", "alors", "mais", "avec", "dans", "sur",
    "pour", "pas", "je", "vous", "nous", "est", "suis", "c'est", "d'accord", "bon", "normalement",
    "franchement", "madame", "monsieur", "exactement", "immédiatement", "urgent", "important",
}
_FR_ACCENTS = set("éèêëàâçùûôîïœ")


def _is_french(tok: str) -> bool:
    w = "".join(ch for ch in tok.lower() if ch.isalnum() or ch in "'’é").replace("’", "'")
    if not w:
        return False
    if w in _FRENCH_WORDS or any(ch in _FR_ACCENTS for ch in w):
        return True
    return w.isdigit()                      # numbers / dates are read by the French voice


def split_code_switch(text: str) -> list[tuple[str, str]]:
    """[(piece, 'fr'|'ar'), ...]: consecutive words of the same language are grouped.
    Punctuation stays with the word before it, so the voice keeps its pauses."""
    pieces: list[list] = []
    for tok in text.split():
        has_letters = any(ch.isalpha() for ch in tok) or any(ch.isdigit() for ch in tok)
        lang = ("fr" if _is_french(tok) else "ar") if has_letters else (pieces[-1][1] if pieces else "ar")
        if pieces and pieces[-1][1] == lang:
            pieces[-1][0] += " " + tok
        else:
            pieces.append([tok, lang])
    return [(t, l) for t, l in pieces]


# ---------------------------------------------------------------------------------------------
# Text to speech. edge-tts = Microsoft neural voices (free, no key, has Tunisian Arabic).
# gTTS stays as the fallback when edge-tts is not installed or offline.
# ---------------------------------------------------------------------------------------------
VOICES = {  # lang -> (female, male)
    "fr": ("fr-FR-DeniseNeural", "fr-FR-HenriNeural"),
    "en": ("en-US-JennyNeural", "en-US-GuyNeural"),
    "ar": ("ar-TN-ReemNeural", "ar-TN-HediNeural"),
}
_FEMALE_NAMES = {"amira", "sarra", "sara", "salma", "ines", "inès", "mariem", "meriem", "maryam", "yasmine", "nour",
                 "rim", "rahma", "olfa", "hela", "houda", "ghada", "emna", "imen", "asma", "fatma", "fatima",
                 "leila", "layla", "nadia", "samia", "sonia", "wafa", "amel", "mouna", "rania", "dorra", "khadija",
                 "aya", "lina", "marwa", "cyrine", "sirine", "hiba", "sabrina", "karima", "hanen", "souad"}


def guess_gender(name: str) -> str:
    first = (name or "").strip().split(" ")[0].lower()
    return "f" if first in _FEMALE_NAMES or (first.endswith("a") and first not in {"mohamed", "ali"}) else "m"


def clean_for_speech(text: str) -> str:
    """Remove what a voice must not read (emojis, markdown, stage directions) and spell out digits groups."""
    import re
    t = re.sub(r"\*[^*]{0,40}\*", " ", text)                       # *soupire*
    t = re.sub(r"[*_`#>~|]", " ", t)
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF]", " ", t)    # emojis
    # long digit strings (card digits, phone): read them in groups of two with a pause
    t = re.sub(r"\b\d{5,}\b", lambda m: ", ".join(m.group()[i:i + 2] for i in range(0, len(m.group()), 2)), t)
    return re.sub(r"\s+", " ", t).strip()


def _edge_bytes(text: str, voice: str, rate: float, pitch: float) -> bytes:
    import asyncio
    import edge_tts

    r = f"{round((rate - 1) * 100):+d}%"
    p = f"{round((pitch - 1) * 100):+d}Hz"

    async def run() -> bytes:
        out = bytearray()
        async for chunk in edge_tts.Communicate(text, voice, rate=r, pitch=p).stream():
            if chunk["type"] == "audio":
                out += chunk["data"]
        return bytes(out)

    return asyncio.run(run())


def _gtts_bytes(text: str, lang: str, slow: bool) -> bytes:
    import io
    from gtts import gTTS
    buf = io.BytesIO()
    gTTS(text=text, lang=lang, slow=slow).write_to_fp(buf)
    return buf.getvalue()


def tts_backend() -> str:
    try:
        import edge_tts  # noqa: F401
        return "edge-tts"
    except ImportError:
        return "gtts"


def text_to_speech(text: str, out_path: str = "client_reply.mp3", lang: str = "fr", slow: bool = False,
                   gender: str = "f", rate: float = 1.0, pitch: float = 1.0, voice_variant: int = 0) -> str:
    """lang 'ar' = Darija written in Arabizi: spoken piece by piece (Tunisian voice + French voice)."""
    text = clean_for_speech(text)
    if slow:
        rate = min(rate, 0.9)
    use_edge = tts_backend() == "edge-tts"
    g = 0 if gender == "f" else 1

    def voice_of(code: str) -> str:
        return VOICES.get(code, VOICES["fr"])[g]

    def one(part: tuple[str, str]) -> bytes:
        piece, code = part
        spoken = arabizi_to_arabic(piece) if code == "ar" else piece
        if use_edge:
            try:
                return _edge_bytes(spoken, voice_of(code), rate, pitch)
            except Exception:
                pass                                # offline / blocked: gTTS below
        return _gtts_bytes(spoken, code, slow)

    parts = split_code_switch(text) if lang == "ar" else [(text, lang)]
    parts = parts or [(text, lang)]
    if len(parts) == 1:
        chunks = [one(parts[0])]
    else:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(4, len(parts))) as pool:
            chunks = list(pool.map(one, parts))      # order is kept
    with open(out_path, "wb") as fh:                 # mp3 frames can simply be appended one after the other
        fh.write(b"".join(chunks))
    return out_path


def voice_turn(sim: ClientSimulatorAgent, st: ConversationState,
               advisor_audio_path: str, out_path: str = "client_reply.mp3") -> tuple[str, str, str]:
    """One spoken exchange. Returns (advisor_text, client_text, client_audio_path)."""
    advisor_text = speech_to_text(advisor_audio_path)
    client_text = sim.reply(st, advisor_text)
    return advisor_text, client_text, text_to_speech(client_text, out_path)
