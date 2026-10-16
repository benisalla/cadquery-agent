import builtins
from types import SimpleNamespace

import pytest

from cadquery_agent import speech


@pytest.fixture(autouse=True)
def clear_model_cache():
    speech._load_model.cache_clear()
    yield
    speech._load_model.cache_clear()


def test_missing_dependency_raises_install_hint(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "faster_whisper":
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(speech.importlib.util, "find_spec", lambda name: None)

    assert speech.is_available() is False
    with pytest.raises(speech.SpeechUnavailableError, match="uv sync --extra voice"):
        speech.transcribe(b"audio", "base.en")


def test_transcribe_joins_segment_texts(monkeypatch):
    calls = []

    class FakeModel:
        def transcribe(self, audio, **options):
            calls.append((audio.read(), options))
            segments = [SimpleNamespace(text=" Create a box "), SimpleNamespace(text="60 by 40. ")]
            return iter(segments), None

    monkeypatch.setattr(speech, "_load_model", lambda name: FakeModel())

    assert speech.transcribe(b"audio", "base.en") == "Create a box 60 by 40."
    assert calls == [(b"audio", {"beam_size": 1, "vad_filter": True})]
