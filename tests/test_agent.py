import asyncio
import base64
import io
from pathlib import Path

import pytest
from PIL import Image

from cadquery_agent.agent import CadQueryAgent, GenerationError
from cadquery_agent.images import ImageInput
from cadquery_agent.rendering import RenderError, RenderResult, WorkerUnavailableError

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 2, 3)"


def png(width: int = 8, height: int = 8) -> ImageInput:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return ImageInput(data=buffer.getvalue(), media_type="image/png")


class FakeBackend:
    def __init__(self, replies: list[str]) -> None:
        self.replies = iter(replies)
        self.calls: list[tuple[list[dict], str]] = []

    async def chat(self, messages: list[dict], model: str) -> str:
        self.calls.append((list(messages), model))
        return next(self.replies)


class FakeRenderer:
    def __init__(self, errors: list[Exception | None]) -> None:
        self.errors = iter(errors)
        self.calls = 0

    async def render(self, source: str) -> RenderResult:
        self.calls += 1
        error = next(self.errors, None)
        if error is not None:
            raise error
        stats = {"size": [1, 2, 3], "volume": 6.0, "solids": 1, "valid": True}
        return RenderResult("run", Path("model.stl"), Path("model.step"), stats)


def test_generates_source_in_one_attempt() -> None:
    backend = FakeBackend([BOX])
    result = asyncio.run(CadQueryAgent(backend, model="coder").generate(prompt="a box"))
    assert result.attempts == 1
    assert result.trace[0].problems == ()
    messages, model = backend.calls[0]
    assert model == "coder"
    assert messages[0]["role"] == "system"
    assert messages[1] == {"role": "user", "content": "a box"}


def test_retry_feeds_problems_back_as_a_chat_turn() -> None:
    backend = FakeBackend(["import os\nresult = os.getcwd()", BOX])
    result = asyncio.run(CadQueryAgent(backend, model="coder").generate(prompt="box"))
    assert result.attempts == 2
    retry_messages = backend.calls[1][0]
    assert [message["role"] for message in retry_messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert retry_messages[2]["content"].startswith("import os")
    assert "import is not allowed: os" in retry_messages[3]["content"]


def test_render_failure_becomes_feedback_and_is_retried() -> None:
    backend = FakeBackend([BOX, BOX])
    renderer = FakeRenderer([RenderError("CAD worker failed: no pending wires")])
    agent = CadQueryAgent(backend, model="coder", renderer=renderer)
    result = asyncio.run(agent.generate(prompt="box"))
    assert renderer.calls == 2
    assert result.trace[0].problems == ("CAD worker failed: no pending wires",)
    assert result.render is not None and result.render.stats["volume"] == 6.0
    assert "no pending wires" in backend.calls[1][0][-1]["content"]


def test_unavailable_worker_is_not_retried() -> None:
    backend = FakeBackend([BOX, BOX])
    renderer = FakeRenderer([WorkerUnavailableError("no container runtime")])
    agent = CadQueryAgent(backend, model="coder", renderer=renderer)
    with pytest.raises(WorkerUnavailableError):
        asyncio.run(agent.generate(prompt="box"))
    assert len(backend.calls) == 1


def test_gives_up_after_max_attempts_with_last_problems() -> None:
    backend = FakeBackend(["result = 1", "result = 1"])
    agent = CadQueryAgent(backend, model="coder", max_attempts=2)
    with pytest.raises(GenerationError, match="cq.Workplane"):
        asyncio.run(agent.generate(prompt="box"))


def test_image_is_described_by_vision_model_then_coded() -> None:
    backend = FakeBackend(["- plate 60 x 40 x 10 mm", BOX])
    agent = CadQueryAgent(backend, model="coder", vision_model="vision")
    image = png()
    result = asyncio.run(agent.generate(image=image))
    (vision_messages, vision_model), (code_messages, code_model) = backend.calls
    assert (vision_model, code_model) == ("vision", "coder")
    assert vision_messages[0]["content"][1]["type"] == "image_url"
    assert "Sketch description:\n- plate 60 x 40 x 10 mm" in code_messages[1]["content"]
    assert result.sketch_description == "- plate 60 x 40 x 10 mm"


def test_image_requires_a_vision_model() -> None:
    image = png()
    with pytest.raises(ValueError, match="vision model"):
        asyncio.run(CadQueryAgent(FakeBackend([]), model="coder").generate(image=image))


def test_requires_an_input() -> None:
    with pytest.raises(ValueError, match="provide"):
        asyncio.run(CadQueryAgent(FakeBackend([]), model="coder").generate())


def test_revision_task_contains_current_script_and_change() -> None:
    backend = FakeBackend([BOX])
    asyncio.run(
        CadQueryAgent(backend, model="coder").generate(
            prompt="make it twice as large",
            existing_source="import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)",
        )
    )
    task = backend.calls[0][0][1]["content"]
    assert "```python\nimport cadquery as cq" in task
    assert "Change request: make it twice as large" in task
    assert "keep every existing feature" in task


def test_large_sketches_are_shrunk_before_the_vision_model() -> None:
    backend = FakeBackend(["- plate 10 x 10 x 2 mm", BOX])
    agent = CadQueryAgent(backend, model="coder", vision_model="vision")
    asyncio.run(agent.generate(image=png(3000, 2000)))
    sent = backend.calls[0][0][-1]["content"][1]["image_url"]["url"]
    data = base64.b64decode(sent.split(",", 1)[1])
    assert max(Image.open(io.BytesIO(data)).size) == 1024


def test_models_can_be_chosen_per_request() -> None:
    backend = FakeBackend([BOX])
    agent = CadQueryAgent(backend, model="coder")
    result = asyncio.run(agent.generate(prompt="box", model="other-coder"))
    assert result.model == "other-coder"
    assert backend.calls[0][1] == "other-coder"


def test_repair_chat_keeps_only_the_latest_failure() -> None:
    backend = FakeBackend(["result = 1", "result = 2", BOX])
    asyncio.run(CadQueryAgent(backend, model="coder", max_attempts=3).generate(prompt="box"))
    assert [len(messages) for messages, _ in backend.calls] == [2, 4, 4]
    assert backend.calls[2][0][2]["content"] == "result = 2"


def test_typed_words_override_the_sketch_description() -> None:
    backend = FakeBackend(["- plate 150 x 75 mm", BOX])
    agent = CadQueryAgent(backend, model="coder", vision_model="vision")
    asyncio.run(agent.generate(prompt="an 80 x 50 mm plate", image=png()))
    task = backend.calls[1][0][1]["content"]
    assert "Sketch description:\n- plate 150 x 75 mm" in task
    assert "Request (it overrides the sketch description): an 80 x 50 mm plate" in task


def test_lint_problems_are_fed_back_before_rendering() -> None:
    halved = "import cadquery as cq\nd = 6\nresult = cq.Workplane('XY').box(9, 9, 2).hole(d / 2)"
    backend = FakeBackend([halved, BOX])
    renderer = FakeRenderer([None])
    result = asyncio.run(
        CadQueryAgent(backend, model="coder", renderer=renderer).generate(prompt="x")
    )
    assert result.trace[0].problems == (
        "line 3: hole() takes the full diameter; do not divide by 2",
    )
    assert renderer.calls == 1


def test_revision_task_states_the_current_measured_size() -> None:
    backend = FakeBackend([BOX])
    asyncio.run(
        CadQueryAgent(backend, model="coder").generate(
            prompt="add a hole", existing_source=BOX, existing_size=[100.0, 60.0, 8.0]
        )
    )
    assert "The current part measures 100 x 60 x 8 mm" in backend.calls[0][0][1]["content"]


def test_unrequested_outline_change_is_questioned_once() -> None:
    backend = FakeBackend([BOX, BOX])
    agent = CadQueryAgent(backend, model="coder", renderer=FakeRenderer([]))
    result = asyncio.run(
        agent.generate(prompt="add a fillet", existing_source=BOX, existing_size=[5.0, 5.0, 3.0])
    )
    assert result.attempts == 2
    assert "the outline changed from 5 x 5 mm to 1 x 2 mm" in result.trace[0].problems[0]
