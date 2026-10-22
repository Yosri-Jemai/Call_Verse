from types import SimpleNamespace

import pytest

from src.stt import transcriber


def test_transcribe_audio_file_returns_text_and_segments(monkeypatch):
    class FakeModel:
        def transcribe(self, path, **kwargs):
            assert kwargs["language"] == "fr"
            assert kwargs["vad_filter"] is True
            return (
                [
                    SimpleNamespace(start=0.123, end=1.456, text=" Bonjour "),
                    SimpleNamespace(start=1.5, end=2.0, text=" Cashy. "),
                ],
                SimpleNamespace(language="fr"),
            )

    monkeypatch.setattr(transcriber, "get_model", lambda: FakeModel())

    result = transcriber.transcribe_audio_file(b"audio", language="fr")

    assert result == {
        "text": "Bonjour Cashy.",
        "language": "fr",
        "segments": [
            {"start": 0.12, "end": 1.46, "text": "Bonjour"},
            {"start": 1.5, "end": 2.0, "text": "Cashy."},
        ],
    }


@pytest.mark.parametrize(
    ("audio_bytes", "language", "message"),
    [
        (b"", None, "empty"),
        (b"audio", "xx", "Language must be one of"),
    ],
)
def test_transcribe_audio_file_rejects_invalid_input(audio_bytes, language, message):
    with pytest.raises(ValueError, match=message):
        transcriber.transcribe_audio_file(audio_bytes, language=language)
