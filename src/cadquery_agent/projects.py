import asyncio
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from cadquery_agent.agent import CadQueryAgent, Events, check_script
from cadquery_agent.images import ImageInput
from cadquery_agent.rendering import DockerCadRenderer


class ProjectNotFoundError(LookupError):
    """The requested local CAD project does not exist."""


class RevisionNotFoundError(LookupError):
    """The requested project revision does not exist."""


class SourceValidationError(ValueError):
    """Manually edited source failed the static, runtime or geometry checks."""


@dataclass(frozen=True, slots=True)
class ProjectRevision:
    number: int
    instruction: str
    code: str
    model: str
    attempts: int
    run_id: str
    created_at: str
    size: list[float] = field(default_factory=list)
    trace: list[list[str]] = field(default_factory=list)


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
    ) -> None:
        self.store = store
        self.agent = agent
        self.renderer = renderer

    async def add_step(
        self,
        project_id: str,
        instruction: str,
        base_revision: int | None = None,
        image: ImageInput | None = None,
        model: str | None = None,
        vision_model: str | None = None,
        on_event: Events | None = None,
    ) -> tuple[CadProject, ProjectRevision]:
        async with self.store.lock_for(project_id):
            project = self.store.get(project_id)
            generated = await self.agent.generate(
                prompt=instruction,
                image=image,
                existing_source=self._base_source(project, base_revision),
                existing_size=self._base_size(project, base_revision),
                model=model,
                vision_model=vision_model,
                on_event=on_event,
            )
            if generated.render is None:
                raise RuntimeError("the project agent must be built with a renderer")
            title = instruction.strip()
            if not title and generated.sketch_description:
                title = "Sketch: " + " ".join(generated.sketch_description.split())[:80]
            revision = ProjectRevision(
                number=len(project.revisions) + 1,
                instruction=title,
                code=generated.code,
                model=generated.model,
                attempts=generated.attempts,
                run_id=generated.render.run_id,
                created_at=datetime.now(UTC).isoformat(),
                size=generated.render.stats.get("size", []),
                trace=[list(attempt.problems) for attempt in generated.trace],
            )
            return self.store.append(project_id, revision), revision

    async def save_source(
        self,
        project_id: str,
        source: str,
        base_revision: int | None = None,
        label: str | None = None,
    ) -> tuple[CadProject, ProjectRevision]:
        normalized_source = source.strip()
        async with self.store.lock_for(project_id):
            project = self.store.get(project_id)
            base_source = self._base_source(project, base_revision)
            if base_source is not None and base_source.strip() == normalized_source:
                raise SourceValidationError("make a code change before running it")
            problems, rendered = await check_script(normalized_source, self.renderer)
            if problems or rendered is None:
                raise SourceValidationError("; ".join(problems))
            selected_number = base_revision
            if selected_number is None and project.revisions:
                selected_number = project.revisions[-1].number
            origin = f"v{selected_number}" if selected_number is not None else "new design"
            revision = ProjectRevision(
                number=len(project.revisions) + 1,
                instruction=label or f"Manual code edit from {origin}",
                code=normalized_source,
                model="manual",
                attempts=1,
                run_id=rendered.run_id,
                created_at=datetime.now(UTC).isoformat(),
                size=rendered.stats.get("size", []),
                trace=[[]],
            )
            return self.store.append(project_id, revision), revision

    def _base_size(self, project: CadProject, number: int | None) -> list[float] | None:
        selected = number or (project.revisions[-1].number if project.revisions else None)
        for revision in project.revisions:
            if revision.number == selected:
                return [round(value, 1) for value in revision.size] or None
        return None

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
