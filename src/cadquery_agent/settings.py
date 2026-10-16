from functools import lru_cache
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CAD_AGENT_",
        extra="ignore",
    )

    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = "qwen2.5-coder:3b"
    vision_model: str = "qwen3.5:4b"
    whisper_model: str = "base.en"
    voice_models: list[str] = ["tiny.en", "base.en", "small.en"]
    # Optional cloud models on Hugging Face: not local, rate-limited, prompts leave the machine.
    hf_token: str = Field(
        default="", validation_alias=AliasChoices("CAD_AGENT_HF_TOKEN", "HF_TOKEN")
    )
    hf_models: list[str] = [
        "Qwen/Qwen2.5-Coder-32B-Instruct",
        "Qwen/Qwen3-Coder-30B-A3B-Instruct",
        "Qwen/Qwen3.5-35B-A3B",
        "Qwen/Qwen3-VL-30B-A3B-Instruct",
        "google/gemma-3-27b-it",
    ]
    timeout_seconds: float = Field(default=180.0, gt=0, le=600)
    max_tokens: int = Field(default=1024, ge=64, le=4096)
    max_attempts: int = Field(default=3, ge=1, le=5)
    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)
    worker_image: str = "cadquery-agent-worker:local"
    render_timeout_seconds: float = Field(default=45.0, gt=0, le=120)

    @field_validator("base_url")
    @classmethod
    def require_loopback_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
            raise ValueError("base_url must be an HTTP localhost/loopback URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, a query, or a fragment")
        return value.rstrip("/")


@lru_cache
def get_settings() -> Settings:
    return Settings()
