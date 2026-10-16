import importlib.util
import io
from functools import lru_cache


class SpeechUnavailableError(RuntimeError):
    pass


def is_available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


@lru_cache(maxsize=2)
def _load_model(model_name: str):
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise SpeechUnavailableError(
            "Voice input needs faster-whisper. Install it with: uv sync --extra voice"
        ) from error
    return WhisperModel(model_name, device="cpu", compute_type="int8")


def transcribe(audio: bytes, model_name: str) -> str:
    segments, _ = _load_model(model_name).transcribe(
        io.BytesIO(audio), beam_size=1, vad_filter=True
    )
    return " ".join(segment.text.strip() for segment in segments).strip()
