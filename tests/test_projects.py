import asyncio
from pathlib import Path

from cadquery_agent.agent import GenerationResult
from cadquery_agent.projects import ProjectStore, ProjectWorkflow, SourceValidationError
from cadquery_agent.rendering import RenderError, RenderResult


class RecordingAgent:
    def __init__(self) -> None:
        self.existing_sources: list[str | None] = []
        self.repairs = 0

    async def generate(
        self,
        prompt: str = "",
        image=None,
        existing_source: str | None = None,
    ) -> GenerationResult:
        del image
        self.existing_sources.append(existing_source)
        size = len(self.existing_sources)
        code = f"import cadquery as cq\nresult = cq.Workplane('XY').box({size}, 1, 1)"
        return GenerationResult(code=code, attempts=1, model="local-test-model")

    async def repair_runtime_failure(
        self,
        source: str,
        error: str,
        instruction: str,
    ) -> GenerationResult:
        del source, error, instruction
        self.repairs += 1
        return GenerationResult(
            code="import cadquery as cq\nresult = cq.Workplane('XY').box(9, 9, 9)",
            attempts=1,
            model="local-test-model",
        )


class RecordingRenderer:
    def __init__(self, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.calls = 0

    async def render(self, source: str) -> RenderResult:
        del source
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise RenderError("invalid workplane operation")
        return RenderResult(
            run_id=f"00000000-0000-0000-0000-{self.calls:012d}",
            stl_path=Path("model.stl"),
            step_path=Path("model.step"),
        )


def test_steps_use_latest_successful_revision_as_context(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Gearbox")
    agent = RecordingAgent()
    workflow = ProjectWorkflow(store, agent, RecordingRenderer())

    first, _ = asyncio.run(workflow.add_step(project.project_id, "Create the housing"))
    second, _ = asyncio.run(workflow.add_step(project.project_id, "Add an axle hole"))

    assert agent.existing_sources == [None, first.revisions[0].code]
    assert len(second.revisions) == 2
    assert store.get(project.project_id) == second


def test_step_can_branch_from_an_older_revision(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Branching bracket")
    agent = RecordingAgent()
    workflow = ProjectWorkflow(store, agent, RecordingRenderer())

    first, _ = asyncio.run(workflow.add_step(project.project_id, "Create the base"))
    asyncio.run(workflow.add_step(project.project_id, "Add a boss"))
    branched, revision = asyncio.run(
        workflow.add_step(
            project.project_id,
            "Use mounting holes instead",
            base_revision=1,
        )
    )

    assert agent.existing_sources[-1] == first.revisions[0].code
    assert revision.number == 3
    assert len(branched.revisions) == 3


def test_runtime_failure_is_repaired_before_revision_is_saved(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Bracket")
    agent = RecordingAgent()
    renderer = RecordingRenderer(fail_once=True)
    workflow = ProjectWorkflow(store, agent, renderer)

    updated, revision = asyncio.run(workflow.add_step(project.project_id, "Create a bracket"))

    assert renderer.calls == 2
    assert agent.repairs == 1
    assert revision.attempts == 2
    assert "box(9, 9, 9)" in revision.code
    assert len(updated.revisions) == 1


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
    assert updated.revisions == (revision,)


def test_invalid_manual_source_is_not_rendered_or_saved(tmp_path: Path) -> None:
    store = ProjectStore(tmp_path)
    project = store.create("Safe editor")
    renderer = RecordingRenderer()
    workflow = ProjectWorkflow(store, RecordingAgent(), renderer)

    try:
        asyncio.run(workflow.save_source(project.project_id, "open('/tmp/file')"))
    except SourceValidationError as error:
        assert "call is not allowed: open" in str(error)
    else:
        raise AssertionError("unsafe source should be rejected")

    assert renderer.calls == 0
    assert store.get(project.project_id).revisions == ()
