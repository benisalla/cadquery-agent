import asyncio
import json
from pathlib import Path

import pytest

from cadquery_agent.agent import Attempt, GenerationResult
from cadquery_agent.projects import ProjectStore, ProjectWorkflow, SourceValidationError
from cadquery_agent.rendering import RenderError, RenderResult, WorkerUnavailableError


def fake_render(number: int) -> RenderResult:
    return RenderResult(
        run_id=f"00000000-0000-0000-0000-{number:012d}",
        stl_path=Path("model.stl"),
        step_path=Path("model.step"),
        stats={"size": [float(number), 1.0, 1.0], "volume": 1.0, "valid": True},
    )


class RecordingAgent:
    def __init__(self) -> None:
        self.existing_sources: list[str | None] = []

    async def generate(
        self,
        prompt: str = "",
        image=None,
        existing_source: str | None = None,
        model: str | None = None,
        vision_model: str | None = None,
        on_event=None,
    ) -> GenerationResult:
        self.existing_sources.append(existing_source)
        size = len(self.existing_sources)
        code = f"import cadquery as cq\nresult = cq.Workplane('XY').box({size}, 1, 1)"
        trace = (Attempt("result = 1", ("no Workplane",)), Attempt(code, ()))
        description = "A flat plate with two holes" if image is not None else None
        return GenerationResult(code, "local-test-model", trace, fake_render(size), description)


class RecordingRenderer:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    async def render(self, source: str) -> RenderResult:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return fake_render(self.calls)


def test_steps_use_latest_revision_as_context(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Gearbox")
    agent = RecordingAgent()
    workflow = ProjectWorkflow(store, agent, RecordingRenderer())

    first, _ = asyncio.run(workflow.add_step(project.project_id, "Create the housing"))
    second, revision = asyncio.run(workflow.add_step(project.project_id, "Add an axle hole"))

    assert agent.existing_sources == [None, first.revisions[0].code]
    assert revision.attempts == 2
    assert revision.trace == [["no Workplane"], []]
    assert revision.size == [2.0, 1.0, 1.0]
    assert store.get(project.project_id) == second


def test_step_can_branch_from_an_older_revision(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Branching bracket")
    agent = RecordingAgent()
    workflow = ProjectWorkflow(store, agent, RecordingRenderer())

    first, _ = asyncio.run(workflow.add_step(project.project_id, "Create the base"))
    asyncio.run(workflow.add_step(project.project_id, "Add a boss"))
    branched, revision = asyncio.run(
        workflow.add_step(project.project_id, "Use mounting holes instead", base_revision=1)
    )

    assert agent.existing_sources[-1] == first.revisions[0].code
    assert revision.number == 3
    assert len(branched.revisions) == 3


def test_sketch_only_step_is_titled_from_the_description(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Sketch")
    workflow = ProjectWorkflow(store, RecordingAgent(), RecordingRenderer())

    _, revision = asyncio.run(workflow.add_step(project.project_id, "", image=object()))

    assert revision.instruction == "Sketch: A flat plate with two holes"


def test_manual_source_is_validated_rendered_and_saved(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Editable block")
    renderer = RecordingRenderer()
    workflow = ProjectWorkflow(store, RecordingAgent(), renderer)
    source = "import cadquery as cq\nsize = 12\nresult = cq.Workplane('XY').box(size, 8, 4)"

    updated, revision = asyncio.run(workflow.save_source(project.project_id, source))

    assert renderer.calls == 1
    assert revision.code == source
    assert revision.model == "manual"
    assert revision.instruction == "Manual code edit from new design"
    assert revision.size == [1.0, 1.0, 1.0]
    assert revision.trace == [[]]
    assert updated.revisions == (revision,)


def test_invalid_manual_source_is_not_rendered_or_saved(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Safe editor")
    renderer = RecordingRenderer()
    workflow = ProjectWorkflow(store, RecordingAgent(), renderer)

    with pytest.raises(SourceValidationError, match="call is not allowed: open"):
        asyncio.run(workflow.save_source(project.project_id, "open('/tmp/file')"))

    assert renderer.calls == 0
    assert store.get(project.project_id).revisions == ()


def test_manual_source_runtime_failure_is_a_validation_error(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Broken edit")
    renderer = RecordingRenderer(RenderError("CAD worker failed: no pending wires"))
    workflow = ProjectWorkflow(store, RecordingAgent(), renderer)
    source = "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)"

    with pytest.raises(SourceValidationError, match="no pending wires"):
        asyncio.run(workflow.save_source(project.project_id, source))
    assert store.get(project.project_id).revisions == ()


def test_manual_source_with_unavailable_worker_propagates(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("No worker")
    workflow = ProjectWorkflow(store, RecordingAgent(), RecordingRenderer(WorkerUnavailableError()))
    source = "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)"

    with pytest.raises(WorkerUnavailableError):
        asyncio.run(workflow.save_source(project.project_id, source))


def test_old_project_files_without_size_or_trace_still_load(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Legacy")
    path = tmp_path / project.project_id / "project.json"
    data = json.loads(path.read_text())
    data["revisions"] = [
        {
            "number": 1,
            "instruction": "Create a block",
            "code": "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 1, 1)",
            "model": "qwen2.5-coder:1.5b",
            "attempts": 1,
            "run_id": "00000000-0000-0000-0000-000000000001",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]
    path.write_text(json.dumps(data))

    revision = store.get(project.project_id).revisions[0]

    assert revision.size == []
    assert revision.trace == []
