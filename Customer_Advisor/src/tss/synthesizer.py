import asyncio
import base64
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from langdetect import DetectorFactory, detect
from langdetect.lang_detect_exception import LangDetectException

logger = logging.getLogger("cashy.tts")
DetectorFactory.seed = 0

VOICES = {
    "fr": "fr-FR-DeniseNeural",
    "en": "en-US-JennyNeural",
    "ar": "ar-SA-ZariyahNeural",
}


def clean_for_tts(text: str) -> str:
    text = re.sub(r"\*{1,3}([^*\n]+?)\*{1,3}", r"\1", text)
    text = re.sub(
        r"^\s*([*\-–—•◦▪›»]|[0-9٠-٩]+[.)]|[٠-٩]+\.?)\s+",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = text.replace("*", "")
    text = re.sub(r"_([^_]+)_", r"\1", text)
    text = re.sub(r"^[\-=*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def detect_lang(text: str, fallback: str = "fr") -> str:
    if not text or not text.strip():
        return fallback

    arabic_chars = len(re.findall(r"[\u0600-\u06FF]", text))
    latin_chars = len(re.findall(r"[A-Za-z]", text))
    if arabic_chars > latin_chars:
        return "ar"

    try:
        language = detect(text.strip())
    except LangDetectException:
        return fallback
    return language if language in VOICES else fallback


def _is_latin_token(token: str) -> bool:
    return bool(re.search(r"[A-Za-zÀ-ÿ]", token))


def split_arabic_latin(text: str) -> list[tuple[str, str]]:
    segments: list[tuple[str, str]] = []
    current_tokens: list[str] = []
    current_type: str | None = None

    for token in text.split():
        token_type = "fr" if _is_latin_token(token) else "ar"
        if token_type == current_type:
            current_tokens.append(token)
            continue
        if current_tokens and current_type:
            segments.append((" ".join(current_tokens), current_type))
        current_tokens = [token]
        current_type = token_type

    if current_tokens and current_type:
        segments.append((" ".join(current_tokens), current_type))
    return segments or [(text.strip(), "ar")]


def _fallback_timings(
    text: str, mp3_size_bytes: int
) -> tuple[list[str], list[float], list[float]]:
    words = text.strip().split()
    if not words:
        return [], [], []
    duration_ms = max((mp3_size_bytes / 16_000) * 1000, 500)
    word_duration = duration_ms / len(words)
    return (
        words,
        [index * word_duration for index in range(len(words))],
        [word_duration] * len(words),
    )


async def _synth_segment_with_timing(
    text: str, lang: str
) -> tuple[bytes, list[str], list[float], list[float]]:
    import edge_tts

    voice = VOICES.get(lang, VOICES["fr"])
    communicate = edge_tts.Communicate(text.strip(), voice, rate="+10%")
    audio_chunks: list[bytes] = []
    words: list[str] = []
    word_times: list[float] = []
    word_durations: list[float] = []

    async for event in communicate.stream():
        if event["type"] == "audio":
            audio_chunks.append(event["data"])
        elif event["type"] == "WordBoundary":
            words.append(event["text"])
            word_times.append(event["offset"] / 10_000)
            word_durations.append(event["duration"] / 10_000)

    mp3_bytes = b"".join(audio_chunks)
    if not words and mp3_bytes:
        logger.warning(
            "No WordBoundary events received for language %s; estimating timings", lang
        )
        words, word_times, word_durations = _fallback_timings(text, len(mp3_bytes))

    return mp3_bytes, words, word_times, word_durations


async def synthesize_async(text: str, lang: str = "auto") -> dict:
    text = clean_for_tts(text)
    if not text:
        return {
            "audio_b64": "",
            "voice": VOICES["fr"],
            "lang": "fr",
            "format": "mp3",
            "segments": [],
            "words": [],
            "wtimes": [],
            "wdurations": [],
        }

    detected = detect_lang(text) if lang == "auto" or lang not in VOICES else lang
    if detected == "ar" and re.search(r"[A-Za-z]{2,}", text):
        segments = split_arabic_latin(text)
        all_mp3: list[bytes] = []
        all_words: list[str] = []
        all_word_times: list[float] = []
        all_word_durations: list[float] = []
        offset_ms = 0.0

        for segment_text, segment_lang in segments:
            if not segment_text.strip():
                continue
            (
                mp3_bytes,
                words,
                word_times,
                word_durations,
            ) = await _synth_segment_with_timing(segment_text, segment_lang)
            all_mp3.append(mp3_bytes)
            all_words.extend(words)
            all_word_times.extend(time + offset_ms for time in word_times)
            all_word_durations.extend(word_durations)

            if word_times and word_durations:
                segment_duration = word_times[-1] + word_durations[-1]
            else:
                segment_duration = max((len(mp3_bytes) / 16_000) * 1000, 100)
            offset_ms += segment_duration

        audio_bytes = b"".join(all_mp3)
        return {
            "audio_b64": base64.b64encode(audio_bytes).decode("utf-8"),
            "voice": VOICES["ar"],
            "lang": "ar",
            "format": "mp3",
            "segments": [
                {"text": part, "lang": part_lang} for part, part_lang in segments
            ],
            "words": all_words,
            "wtimes": all_word_times,
            "wdurations": all_word_durations,
        }

    mp3_bytes, words, word_times, word_durations = await _synth_segment_with_timing(
        text, detected
    )
    return {
        "audio_b64": base64.b64encode(mp3_bytes).decode("utf-8"),
        "voice": VOICES[detected],
        "lang": detected,
        "format": "mp3",
        "segments": [{"text": text, "lang": detected}],
        "words": words,
        "wtimes": word_times,
        "wdurations": word_durations,
    }


def synthesize(text: str, lang: str = "auto") -> dict:
    """Synchronous entry point for callers outside an existing event loop."""
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, synthesize_async(text, lang)).result()
