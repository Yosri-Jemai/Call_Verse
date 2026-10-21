"""
Optional audio channel: advisor voice -> text (Whisper), client text -> voice (gTTS).
Needs: pip install openai-whisper gTTS   (+ ffmpeg installed on your system)
Imports are lazy so the rest of the project works without these libraries.
"""
from __future__ import annotations

from functools import lru_cache

from client_simulator import ClientSimulatorAgent, ConversationState


@lru_cache(maxsize=2)
def _whisper_model(size: str):
    """Load each Whisper model once (loading is slow)."""
    import whisper
    return whisper.load_model(size)


def speech_to_text(audio_path: str, model_size: str = "base", language: str | None = None) -> str:
    """language=None (or "auto") lets Whisper detect it, which suits French / Darija code-switching.
    Whisper has no dedicated Tunisian model: expect transcription errors on heavy Darija."""
    model = _whisper_model(model_size)
    opts = {} if language in (None, "", "auto") else {"language": language}
    return model.transcribe(audio_path, fp16=False, **opts)["text"].strip()


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


def text_to_speech(text: str, out_path: str = "client_reply.mp3", lang: str = "fr",
                   slow: bool = False) -> str:
    from gtts import gTTS
    spoken = arabizi_to_arabic(text) if lang == "ar" else text   # "ar" = Darija (written in Arabizi)
    gTTS(text=spoken, lang=lang, slow=slow).save(out_path)       # gTTS prosody is limited to slow/normal
    return out_path


def voice_turn(sim: ClientSimulatorAgent, st: ConversationState,
               advisor_audio_path: str, out_path: str = "client_reply.mp3") -> tuple[str, str, str]:
    """One spoken exchange. Returns (advisor_text, client_text, client_audio_path)."""
    advisor_text = speech_to_text(advisor_audio_path)
    client_text = sim.reply(st, advisor_text)
    return advisor_text, client_text, text_to_speech(client_text, out_path)
