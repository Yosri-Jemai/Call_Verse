import os
import tempfile
from threading import Lock
from typing import Any

_model: Any | None = None
_model_lock = Lock()
SUPPORTED_LANGUAGES = {"ar", "en", "fr"}


def get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel

                _model = WhisperModel("small", device="cpu", compute_type="int8")
    return _model


def transcribe_audio_file(audio_bytes: bytes, language: str | None = None) -> dict:
    """Transcribe uploaded audio locally and return its text and timestamped segments."""
    if not audio_bytes:
        raise ValueError("The audio recording is empty.")
    if language is not None and language not in SUPPORTED_LANGUAGES:
        raise ValueError("Language must be one of: ar, en, fr.")

    with tempfile.NamedTemporaryFile(suffix=".audio", delete=False) as temporary_file:
        temporary_file.write(audio_bytes)
        temporary_path = temporary_file.name

    try:
        segments, info = get_model().transcribe(
            temporary_path,
            language=language,
            beam_size=5,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        segment_list = []
        full_text = []
        for segment in segments:
            text = segment.text.strip()
            full_text.append(text)
            segment_list.append(
                {
                    "start": round(segment.start, 2),
                    "end": round(segment.end, 2),
                    "text": text,
                }
            )
        return {
            "text": " ".join(full_text).strip(),
            "language": info.language,
            "segments": segment_list,
        }
    finally:
        os.unlink(temporary_path)
