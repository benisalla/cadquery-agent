import base64
from dataclasses import dataclass
from typing import Protocol

import httpx

from cadquery_agent.images import ImageInput
from cadquery_agent.settings import Settings


class ModelServerError(RuntimeError):
    """The configured local model server could not complete a request."""


class ModelBackend(Protocol):
    async def generate(self, prompt: str, image: ImageInput | None = None) -> str: ...


@dataclass(slots=True)
class OpenAICompatibleBackend:
    settings: Settings
    transport: httpx.AsyncBaseTransport | None = None

    async def generate(self, prompt: str, image: ImageInput | None = None) -> str:
        user_content: str | list[dict[str, object]] = prompt
        if image is not None:
            encoded = base64.b64encode(image.data).decode("ascii")
            user_content = [
                {"type": "text", "text": prompt},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{image.media_type};base64,{encoded}"},
                },
            ]
        payload = {
            "model": self.settings.model,
            "messages": [{"role": "user", "content": user_content}],
            "temperature": 0,
            "max_tokens": self.settings.max_tokens,
        }
        try:
            async with httpx.AsyncClient(
                base_url=self.settings.base_url,
                timeout=self.settings.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post("/chat/completions", json=payload)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise ModelServerError(f"local model request failed: {error}") from error

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ModelServerError("local model returned an invalid chat-completions response") from error
        if not isinstance(content, str) or not content.strip():
            raise ModelServerError("local model returned empty content")
        return content
