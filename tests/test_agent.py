import asyncio

import pytest

from cadquery_agent.agent import CadQueryAgent


class FakeBackend:
    def __init__(self, responses: list[str]) -> None:
        self.responses = iter(responses)
        self.prompts: list[str] = []

    async def generate(self, prompt: str, image=None) -> str:
        self.prompts.append(prompt)
        return next(self.responses)


def test_generates_text_only_source() -> None:
    backend = FakeBackend(["import cadquery as cq\nresult = cq.Workplane('XY').box(1, 2, 3)"])
    result = asyncio.run(
        CadQueryAgent(backend, model="qwen2.5-coder:1.5b").generate(prompt="a box")
    )
    assert result.attempts == 1
    assert "Workplane" in result.code


def test_retries_source_that_fails_static_checks() -> None:
    backend = FakeBackend(
        [
            "import os\nresult = os.getcwd()",
            "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 2, 3)",
        ]
    )
    result = asyncio.run(CadQueryAgent(backend, model="qwen2.5vl:3b").generate(prompt="box"))
    assert result.attempts == 2
    assert "import is not allowed: os" in backend.prompts[1]


def test_requires_an_input() -> None:
    with pytest.raises(ValueError, match="provide"):
        asyncio.run(CadQueryAgent(FakeBackend([]), model="qwen2.5vl:3b").generate())


def test_revision_prompt_contains_existing_source_and_requested_change() -> None:
    backend = FakeBackend(["import cadquery as cq\nresult = cq.Workplane('XY').box(2, 2, 2)"])
    asyncio.run(
        CadQueryAgent(backend, model="qwen2.5-coder:1.5b").generate(
            prompt="make it twice as large",
            existing_source="import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)",
        )
    )
    assert "Existing validated script" in backend.prompts[0]
    assert "box(1, 1, 1)" in backend.prompts[0]
    assert "make it twice as large" in backend.prompts[0]
