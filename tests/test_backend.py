import asyncio
import base64
import json

import httpx

from cadquery_agent.backend import OpenAICompatibleBackend
from cadquery_agent.images import ImageInput
from cadquery_agent.settings import Settings


def test_sends_openai_compatible_image_request_to_loopback() -> None:
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 2, 3)"
                        }
                    }
                ]
            },
        )

    settings = Settings(model="qwen2.5vl:3b")
    backend = OpenAICompatibleBackend(settings, transport=httpx.MockTransport(handler))
    image = ImageInput(data=b"image-bytes", media_type="image/png")
    result = asyncio.run(backend.generate("build this", image=image))

    assert result.startswith("import cadquery")
    assert captured["url"] == "http://127.0.0.1:11434/v1/chat/completions"
    body = captured["body"]
    image_url = body["messages"][0]["content"][1]["image_url"]["url"]
    assert image_url == "data:image/png;base64," + base64.b64encode(b"image-bytes").decode()
