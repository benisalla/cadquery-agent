import asyncio
import base64
import json

import httpx
import pytest

from cadquery_agent.backend import ModelServerError, OpenAICompatibleBackend, user_message
from cadquery_agent.images import ImageInput
from cadquery_agent.settings import Settings


def test_user_message_is_plain_text_without_an_image() -> None:
    assert user_message("hi") == {"role": "user", "content": "hi"}


def test_chat_posts_messages_and_model_to_loopback() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "a plate"}}]})

    backend = OpenAICompatibleBackend(Settings(), transport=httpx.MockTransport(handler))
    image = ImageInput(data=b"image-bytes", media_type="image/png")
    reply = asyncio.run(backend.chat([user_message("describe", image)], "qwen2.5vl:3b"))

    assert reply == "a plate"
    assert captured["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    body = captured["body"]
    assert body["model"] == "qwen2.5vl:3b"
    image_url = body["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url == "data:image/png;base64," + base64.b64encode(b"image-bytes").decode()


def test_cloud_models_go_to_hugging_face_with_the_token() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["model"] = json.loads(request.content)["model"]
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    backend = OpenAICompatibleBackend(
        Settings(hf_token="secret"), transport=httpx.MockTransport(handler)
    )
    asyncio.run(backend.chat([{"role": "user", "content": "hi"}], "hf:Qwen/Qwen3.5-35B-A3B"))

    assert captured["url"] == "https://router.huggingface.co/v1/chat/completions"
    assert captured["auth"] == "Bearer secret"
    assert captured["model"] == "Qwen/Qwen3.5-35B-A3B"


def test_cloud_models_are_listed_only_with_a_token() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "qwen2.5-coder:3b"}]})

    transport = httpx.MockTransport(handler)
    local = asyncio.run(OpenAICompatibleBackend(Settings(hf_token=""), transport).list_models())
    both = asyncio.run(OpenAICompatibleBackend(Settings(hf_token="t"), transport).list_models())
    assert local == ["qwen2.5-coder:3b"]
    assert "hf:Qwen/Qwen2.5-Coder-32B-Instruct" in both


def test_slow_model_reports_a_timeout_not_an_unreachable_server() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    settings = Settings(timeout_seconds=90)
    backend = OpenAICompatibleBackend(settings, transport=httpx.MockTransport(handler))
    with pytest.raises(ModelServerError, match="took longer than 90 s"):
        asyncio.run(backend.chat([user_message("hi")], "coder"))
