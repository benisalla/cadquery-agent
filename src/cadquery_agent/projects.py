import asyncio
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from cadquery_agent.agent import CadQueryAgent, GenerationResult
from cadquery_agent.rendering import DockerCadRenderer, RenderError, RenderResult
from cadquery_agent.validation import validate_cadquery_source


class ProjectNotFoundError(LookupError):
    """The requested local CAD project does not exist."""


class RevisionNotFoundError(LookupError):
    """The requested project revision does not exist."""


class SourceValidationError(ValueError):
    """Manually edited source failed the deterministic safety checks."""


@dataclass(frozen=True, slots=True)
class ProjectRevision:
    number: int
    instruction: str
    code: str
    model: str
    attempts: int
    run_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class CadProject:
    project_id: str
    name: str
    created_at: str
    revisions: tuple[ProjectRevision, ...]


class ProjectStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or Path("artifacts/projects")).resolve()
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, project_id: str) -> asyncio.Lock:
        return self._locks.setdefault(project_id, asyncio.Lock())

    def create(self, name: str) -> CadProject:
        project = CadProject(
            project_id=str(uuid4()),
            name=name.strip(),
            created_at=datetime.now(UTC).isoformat(),
            revisions=(),
        )
        self._write(project)
        return project

    def get(self, project_id: str) -> CadProject:
        path = self._path(project_id)
        if not path.is_file():
            raise ProjectNotFoundError("CAD project not found")
        data = json.loads(path.read_text(encoding="utf-8"))
        revisions = tuple(ProjectRevision(**item) for item in data["revisions"])
        return CadProject(
            project_id=data["project_id"],
            name=data["name"],
            created_at=data["created_at"],
            revisions=revisions,
        )

    def append(self, project_id: str, revision: ProjectRevision) -> CadProject:
        project = self.get(project_id)
        updated = CadProject(
            project_id=project.project_id,
            name=project.name,
            created_at=project.created_at,
            revisions=(*project.revisions, revision),
        )
        self._write(updated)
        return updated

    def _path(self, project_id: str) -> Path:
        try:
            safe_id = str(UUID(project_id))
        except ValueError as error:
            raise ProjectNotFoundError("CAD project not found") from error
        return self.root / safe_id / "project.json"

    def _write(self, project: CadProject) -> None:
        path = self._path(project.project_id)
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        payload = {
            "project_id": project.project_id,
            "name": project.name,
            "created_at": project.created_at,
            "revisions": [asdict(revision) for revision in project.revisions],
        }
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temporary.replace(path)


class ProjectWorkflow:
    def __init__(
        self,
        store: ProjectStore,
        agent: CadQueryAgent,
        renderer: DockerCadRenderer,
        runtime_retries: int = 2,
    ) -> None:
        self.store = store
        self.agent = agent
        self.renderer = renderer
        self.runtime_retries = runtime_retries

    async def add_step(
        self,
        project_id: str,
        instruction: str,
        base_revision: int | None = None,
    ) -> tuple[CadProject, ProjectRevision]:
        lock = self.store.lock_for(project_id)
        async with lock:
            project = self.store.get(project_id)
            existing_source = self._base_source(project, base_revision)
            generated = await self.agent.generate(
                prompt=instruction,
                existing_source=existing_source,
            )
            rendered, generated = await self._render_with_repairs(generated, instruction)
            revision = ProjectRevision(
                number=len(project.revisions) + 1,
                instruction=instruction.strip(),
                code=generated.code,
                model=generated.model,
                attempts=generated.attempts,
                run_id=rendered.run_id,
                created_at=datetime.now(UTC).isoformat(),
            )
            return self.store.append(project_id, revision), revision

    async def save_source(
        self,
        project_id: str,
        source: str,
        base_revision: int | None = None,
    ) -> tuple[CadProject, ProjectRevision]:
        normalized_source = source.strip()
        validation = validate_cadquery_source(normalized_source)
        if not validation.is_valid:
            raise SourceValidationError("; ".join(validation.errors))

        lock = self.store.lock_for(project_id)
        async with lock:
            project = self.store.get(project_id)
            base_source = self._base_source(project, base_revision)
            if base_source is not None and base_source.strip() == normalized_source:
                raise SourceValidationError("make a code change before running it")
            rendered = await self.renderer.render(normalized_source)
            selected_number = base_revision
            if selected_number is None and project.revisions:
                selected_number = project.revisions[-1].number
            origin = f"v{selected_number}" if selected_number is not None else "new design"
            revision = ProjectRevision(
                number=len(project.revisions) + 1,
                instruction=f"Manual code edit from {origin}",
                code=normalized_source,
                model="manual",
                attempts=1,
                run_id=rendered.run_id,
                created_at=datetime.now(UTC).isoformat(),
            )
            return self.store.append(project_id, revision), revision

    def _base_source(self, project: CadProject, number: int | None) -> str | None:
        if not project.revisions:
            if number is not None:
                raise RevisionNotFoundError("base revision does not exist")
            return None
        selected = number or project.revisions[-1].number
        for revision in project.revisions:
            if revision.number == selected:
                return revision.code
        raise RevisionNotFoundError("base revision does not exist")

    async def _render_with_repairs(
        self,
        generated: GenerationResult,
        instruction: str,
    ) -> tuple[RenderResult, GenerationResult]:
        total_attempts = generated.attempts
        for attempt in range(self.runtime_retries + 1):
            try:
                rendered = await self.renderer.render(generated.code)
                if total_attempts != generated.attempts:
                    generated = GenerationResult(
                        code=generated.code,
                        attempts=total_attempts,
                        model=generated.model,
                    )
                return rendered, generated
            except RenderError as error:
                if attempt == self.runtime_retries:
                    raise
                generated = await self.agent.repair_runtime_failure(
                    source=generated.code,
                    error=str(error),
                    instruction=instruction,
                )
                total_attempts += generated.attempts
        raise AssertionError("unreachable")
