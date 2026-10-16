import asyncio
import json
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, HTMLResponse, StreamingResponse
from starlette.staticfiles import StaticFiles

from cadquery_agent.agent import CadQueryAgent, GenerationError
from cadquery_agent.backend import ModelServerError, OpenAICompatibleBackend, is_cloud
from cadquery_agent.images import validate_image_bytes
from cadquery_agent.models import profile_for
from cadquery_agent.projects import (
    CadProject,
    ProjectNotFoundError,
    ProjectStore,
    ProjectWorkflow,
    RevisionNotFoundError,
    SourceValidationError,
)
from cadquery_agent.rendering import DockerCadRenderer, WorkerUnavailableError, artifact_path
from cadquery_agent.settings import Settings, get_settings
from cadquery_agent.speech import SpeechUnavailableError, is_available, transcribe

_MAX_AUDIO_BYTES = 25 * 1024 * 1024


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ConfigResponse(BaseModel):
    model: str
    vision_model: str
    server: str
    voice: bool


class ModelInfo(BaseModel):
    name: str
    vision: bool
    local: bool


class ModelsResponse(BaseModel):
    models: list[ModelInfo]
    model: str
    vision_model: str
    voice_models: list[str]
    voice_model: str
    cloud: bool


class TranscriptionResponse(BaseModel):
    text: str


class CreateProjectRequest(BaseModel):
    name: str = Field(default="Untitled design", min_length=1, max_length=80)


class ProjectSourceRequest(BaseModel):
    code: str = Field(min_length=1, max_length=100_000)
    base_revision: int | None = Field(default=None, ge=1)
    label: str | None = Field(default=None, max_length=200)


class RevisionResponse(BaseModel):
    number: int
    instruction: str
    code: str
    model: str
    attempts: int
    run_id: str
    created_at: str
    stl_url: str
    step_url: str
    size: list[float]
    trace: list[list[str]]


class ProjectResponse(BaseModel):
    project_id: str
    name: str
    created_at: str
    revisions: list[RevisionResponse]


def get_renderer(settings: Annotated[Settings, Depends(get_settings)]) -> DockerCadRenderer:
    return DockerCadRenderer(settings)


def get_agent(
    settings: Annotated[Settings, Depends(get_settings)],
    renderer: Annotated[DockerCadRenderer, Depends(get_renderer)],
) -> CadQueryAgent:
    return CadQueryAgent(
        backend=OpenAICompatibleBackend(settings),
        model=settings.model,
        vision_model=settings.vision_model,
        renderer=renderer,
        max_attempts=settings.max_attempts,
    )


_PROJECT_STORE = ProjectStore()


def get_project_store() -> ProjectStore:
    return _PROJECT_STORE


def get_project_workflow(
    store: Annotated[ProjectStore, Depends(get_project_store)],
    agent: Annotated[CadQueryAgent, Depends(get_agent)],
    renderer: Annotated[DockerCadRenderer, Depends(get_renderer)],
) -> ProjectWorkflow:
    return ProjectWorkflow(store=store, agent=agent, renderer=renderer)


def project_response(project: CadProject) -> ProjectResponse:
    return ProjectResponse(
        project_id=project.project_id,
        name=project.name,
        created_at=project.created_at,
        revisions=[
            RevisionResponse(
                number=revision.number,
                instruction=revision.instruction,
                code=revision.code,
                model=revision.model,
                attempts=revision.attempts,
                run_id=revision.run_id,
                created_at=revision.created_at,
                stl_url=f"/artifacts/{revision.run_id}/model.stl",
                step_url=f"/artifacts/{revision.run_id}/model.step",
                size=revision.size,
                trace=revision.trace,
            )
            for revision in project.revisions
        ],
    )


app = FastAPI(title="CadQuery Studio", version="0.4.0")
_WEB = Path(__file__).with_name("web")
_INDEX_HTML = _WEB / "index.html"
app.mount("/static", StaticFiles(directory=_WEB), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def interface() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML.read_text(encoding="utf-8"))


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.get("/config", response_model=ConfigResponse)
async def config(settings: Annotated[Settings, Depends(get_settings)]) -> ConfigResponse:
    return ConfigResponse(
        model=settings.model,
        vision_model=settings.vision_model,
        server=settings.base_url,
        voice=is_available(),
    )


@app.get("/models", response_model=ModelsResponse)
async def list_models(settings: Annotated[Settings, Depends(get_settings)]) -> ModelsResponse:
    try:
        names = await OpenAICompatibleBackend(settings).list_models()
    except ModelServerError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return ModelsResponse(
        models=[
            ModelInfo(name=name, vision=profile_for(name).vision, local=not is_cloud(name))
            for name in names
        ],
        model=settings.model,
        vision_model=settings.vision_model,
        voice_models=settings.voice_models,
        voice_model=settings.whisper_model,
        cloud=bool(settings.hf_token),
    )


@app.post("/projects", response_model=ProjectResponse, status_code=201)
async def create_project(
    request: CreateProjectRequest,
    store: Annotated[ProjectStore, Depends(get_project_store)],
) -> ProjectResponse:
    return project_response(store.create(request.name))


@app.get("/projects/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    store: Annotated[ProjectStore, Depends(get_project_store)],
) -> ProjectResponse:
    try:
        project = store.get(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return project_response(project)


def error_status(error: Exception) -> int:
    """Map harness failures to HTTP status codes; unexpected errors propagate."""
    if isinstance(error, ProjectNotFoundError | RevisionNotFoundError):
        return 404
    if isinstance(error, WorkerUnavailableError | SpeechUnavailableError):
        return 503
    if isinstance(error, ModelServerError | GenerationError):
        return 502
    if isinstance(error, ValueError):
        return 422
    raise error


async def step_form(
    settings: Annotated[Settings, Depends(get_settings)],
    instruction: Annotated[str, Form(max_length=8_000)] = "",
    base_revision: Annotated[int | None, Form(ge=1)] = None,
    model: Annotated[str | None, Form(max_length=200)] = None,
    vision_model: Annotated[str | None, Form(max_length=200)] = None,
    image: Annotated[UploadFile | None, File()] = None,
) -> dict:
    """The fields of one build step, shared by /steps and /steps/stream."""
    if not instruction.strip() and image is None:
        raise HTTPException(status_code=422, detail="provide an instruction, an image, or both")
    sketch = None
    if image is not None:
        data = await image.read(settings.max_image_bytes + 1)
        try:
            sketch = validate_image_bytes(data, settings.max_image_bytes)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "instruction": instruction,
        "base_revision": base_revision,
        "model": model,
        "vision_model": vision_model,
        "image": sketch,
    }


@app.post("/projects/{project_id}/steps", response_model=ProjectResponse)
async def add_project_step(
    project_id: str,
    step: Annotated[dict, Depends(step_form)],
    workflow: Annotated[ProjectWorkflow, Depends(get_project_workflow)],
) -> ProjectResponse:
    try:
        project, _ = await workflow.add_step(project_id=project_id, **step)
    except Exception as error:
        raise HTTPException(status_code=error_status(error), detail=str(error)) from error
    return project_response(project)


@app.post("/projects/{project_id}/steps/stream")
async def stream_project_step(
    project_id: str,
    step: Annotated[dict, Depends(step_form)],
    workflow: Annotated[ProjectWorkflow, Depends(get_project_workflow)],
) -> StreamingResponse:
    """Same as /steps, but streams each harness stage as one JSON line."""
    try:
        workflow.store.get(project_id)
    except ProjectNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    events: asyncio.Queue[dict | None] = asyncio.Queue()

    async def run() -> None:
        try:
            project, _ = await workflow.add_step(
                project_id=project_id, on_event=events.put_nowait, **step
            )
            events.put_nowait({"event": "done", "project": project_response(project).model_dump()})
        except (LookupError, RuntimeError, ValueError) as error:
            status = error_status(error)
            events.put_nowait({"event": "error", "status": status, "detail": str(error)})
        finally:
            events.put_nowait(None)

    async def lines():
        task = asyncio.create_task(run())
        while (event := await events.get()) is not None:
            yield json.dumps(event) + "\n"
        await task

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@app.post("/projects/{project_id}/source", response_model=ProjectResponse)
async def save_project_source(
    project_id: str,
    request: ProjectSourceRequest,
    workflow: Annotated[ProjectWorkflow, Depends(get_project_workflow)],
) -> ProjectResponse:
    try:
        project, _ = await workflow.save_source(
            project_id=project_id,
            source=request.code,
            base_revision=request.base_revision,
            label=request.label,
        )
    except (ProjectNotFoundError, RevisionNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SourceValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except WorkerUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return project_response(project)


@app.post("/transcribe", response_model=TranscriptionResponse)
async def transcribe_audio(
    audio: Annotated[UploadFile, File()],
    settings: Annotated[Settings, Depends(get_settings)],
    model: Annotated[str | None, Form()] = None,
) -> TranscriptionResponse:
    model = model or settings.whisper_model
    if model not in {settings.whisper_model, *settings.voice_models}:
        raise HTTPException(status_code=422, detail=f"unknown voice model: {model}")
    data = await audio.read(_MAX_AUDIO_BYTES + 1)
    if not data or len(data) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=422, detail="audio must be between 1 byte and 25 MB")
    try:
        text = await run_in_threadpool(transcribe, data, model)
    except SpeechUnavailableError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=f"could not decode audio: {error}") from error
    return TranscriptionResponse(text=text)


@app.get("/artifacts/{run_id}/model.{extension}", include_in_schema=False)
async def download_artifact(run_id: str, extension: Literal["stl", "step"]) -> FileResponse:
    try:
        path = artifact_path(Path("artifacts/runs"), run_id, extension)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="model artifact not found")
    media_type = "model/stl" if extension == "stl" else "model/step"
    # Every render gets a new run id, so its files never change and can be cached forever.
    headers = {"Cache-Control": "public, max-age=31536000, immutable"}
    return FileResponse(path, media_type=media_type, filename=path.name, headers=headers)
