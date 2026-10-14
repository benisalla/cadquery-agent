import pytest
from pydantic import ValidationError

from cadquery_agent.settings import Settings


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/v1",
        "http://192.168.1.10:11434/v1",
        "file:///tmp/model",
    ],
)
def test_rejects_non_loopback_model_servers(url: str) -> None:
    with pytest.raises(ValidationError, match="loopback"):
        Settings(base_url=url)


def test_accepts_common_loopback_urls() -> None:
    assert Settings(base_url="http://localhost:8000/v1").base_url.endswith("/v1")
    assert Settings(base_url="http://127.0.0.1:11434/v1/").base_url.endswith("/v1")
