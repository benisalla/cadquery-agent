import base64
from dataclasses import dataclass
from typing import Protocol

import httpx

from cadquery_agent.images import ImageInput
from cadquery_agent.models import profile_for
from cadquery_agent.settings import Settings

CLOUD_PREFIX = "hf:"
HF_ROUTER_URL = "https://router.huggingface.co/v1"


def is_cloud(model: str) -> bool:
    return model.startswith(CLOUD_PREFIX)


class ModelServerError(RuntimeError):
    """The model server could not complete a request."""


class ModelBackend(Protocol):
    async def chat(self, messages: list[dict], model: str) -> str: ...


def user_message(text: str, image: ImageInput | None = None) -> dict:
    if image is None:
        return {"role": "user", "content": text}
    encoded = base64.b64encode(image.data).decode("ascii")
    url = f"data:{image.media_type};base64,{encoded}"
    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": url}},
        ],
    }


@dataclass(slots=True)
class OpenAICompatibleBackend:
    settings: Settings
    transport: httpx.AsyncBaseTransport | None = None

    async def chat(self, messages: list[dict], model: str) -> str:
        cloud = is_cloud(model)
        name = model.removeprefix(CLOUD_PREFIX)
        profile = profile_for(name)
        payload = {
            "model": name,
            "messages": messages,
            "temperature": profile.temperature,
            "top_p": profile.top_p,
            "max_tokens": self.settings.max_tokens,
        }
        if profile.no_think and not cloud:
            payload["reasoning_effort"] = "none"
        body = await self._request("POST", "/chat/completions", cloud=cloud, json=payload)
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise ModelServerError(
                "model server returned an invalid chat-completions response"
            ) from error
        if not isinstance(content, str) or not content.strip():
            raise ModelServerError("model returned empty content")
        return content

    async def list_models(self) -> list[str]:
        body = await self._request("GET", "/models")
        local = sorted(item["id"] for item in body.get("data", []))
        cloud = [CLOUD_PREFIX + name for name in self.settings.hf_models]
        return local + cloud if self.settings.hf_token else local

    async def _request(self, method: str, path: str, cloud: bool = False, **kwargs) -> dict:
        base_url, headers = self.settings.base_url, {}
        if cloud:
            if not self.settings.hf_token:
                raise ModelServerError("cloud models need HF_TOKEN in .env")
            base_url, headers = HF_ROUTER_URL, {"Authorization": f"Bearer {self.settings.hf_token}"}
        try:
            async with httpx.AsyncClient(
                base_url=base_url,
                headers=headers,
                timeout=self.settings.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.TimeoutException as error:
            seconds = self.settings.timeout_seconds
            raise ModelServerError(
                f"the model took longer than {seconds:g} s; the computer may be busy, "
                "try again or pick a smaller model"
            ) from error
        except httpx.HTTPError as error:
            raise ModelServerError(f"model server is unreachable: {error}") from error
        if response.is_error:
            detail = response.text[:300]
            raise ModelServerError(f"model server error {response.status_code}: {detail}")
        try:
            return response.json()
        except ValueError as error:
            raise ModelServerError("model server returned invalid JSON") from error
