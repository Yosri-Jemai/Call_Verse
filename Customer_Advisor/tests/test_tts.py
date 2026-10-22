import asyncio
import base64
import sys
from types import SimpleNamespace

from src.tss import synthesizer


def test_clean_for_tts_removes_markdown():
    assert (
        synthesizer.clean_for_tts("## Bonjour\n\n- **Cashy** est là.")
        == "Bonjour\nCashy est là."
    )


def test_split_arabic_latin_groups_consecutive_tokens():
    assert synthesizer.split_arabic_latin("مرحبا Cashy support") == [
        ("مرحبا", "ar"),
        ("Cashy support", "fr"),
    ]


def test_fallback_timings_estimate_word_positions_from_audio_size():
    words, word_times, word_durations = synthesizer._fallback_timings(
        "Hello Cashy", 16_000
    )

    assert words == ["Hello", "Cashy"]
    assert word_times == [0.0, 500.0]
    assert word_durations == [500.0, 500.0]


def test_synthesize_async_returns_audio_and_word_timings(monkeypatch):
    class FakeCommunicate:
        def __init__(self, text, voice, rate):
            assert text == "Hello Cashy."
            assert voice == "en-US-JennyNeural"
            assert rate == "+10%"

        async def stream(self):
            yield {"type": "audio", "data": b"mp3"}
            yield {
                "type": "WordBoundary",
                "text": "Hello",
                "offset": 0,
                "duration": 4_500_000,
            }

    monkeypatch.setitem(
        sys.modules, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate)
    )
    result = asyncio.run(synthesizer.synthesize_async("Hello Cashy.", lang="en"))

    assert base64.b64decode(result["audio_b64"]) == b"mp3"
    assert result["lang"] == "en"
    assert result["words"] == ["Hello"]
    assert result["wtimes"] == [0.0]
    assert result["wdurations"] == [450.0]


def test_synthesize_async_offsets_mixed_arabic_and_latin_timings(monkeypatch):
    class FakeCommunicate:
        def __init__(self, text, voice, rate):
            self.text = text
            self.voice = voice
            assert rate == "+10%"

        async def stream(self):
            yield {"type": "audio", "data": self.voice.encode()}
            yield {
                "type": "WordBoundary",
                "text": self.text,
                "offset": 0,
                "duration": 5_000_000,
            }

    monkeypatch.setitem(
        sys.modules, "edge_tts", SimpleNamespace(Communicate=FakeCommunicate)
    )
    result = asyncio.run(synthesizer.synthesize_async("مرحبا Cashy", lang="ar"))

    assert result["lang"] == "ar"
    assert result["segments"] == [
        {"text": "مرحبا", "lang": "ar"},
        {"text": "Cashy", "lang": "fr"},
    ]
    assert result["wtimes"] == [0.0, 500.0]
    assert base64.b64decode(result["audio_b64"]) == (
        b"ar-SA-ZariyahNeuralfr-FR-DeniseNeural"
    )
