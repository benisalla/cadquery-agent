from functools import lru_cache
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from cadquery_agent.models import DEFAULT_MODEL, require_supported_model

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CAD_AGENT_",
        extra="ignore",
    )

    base_url: str = "http://127.0.0.1:11434/v1"
    model: str = DEFAULT_MODEL
    timeout_seconds: float = Field(default=180.0, gt=0, le=600)
    max_tokens: int = Field(default=1024, ge=64, le=4096)
    max_attempts: int = Field(default=3, ge=1, le=5)
    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=25 * 1024 * 1024)

    @field_validator("base_url")
    @classmethod
    def require_loopback_url(cls, value: str) -> str:
        parsed = urlparse(value)
        if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS:
            raise ValueError("base_url must be an HTTP localhost/loopback URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain credentials, a query, or a fragment")
        return value.rstrip("/")

    @field_validator("model")
    @classmethod
    def require_reviewed_model(cls, value: str) -> str:
        require_supported_model(value)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
