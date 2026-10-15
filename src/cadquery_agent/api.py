from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.responses import FileResponse, HTMLResponse

from cadquery_agent.agent import CadQueryAgent, GenerationError
from cadquery_agent.backend import ModelServerError, OpenAICompatibleBackend
from cadquery_agent.images import validate_image_bytes
from cadquery_agent.projects import (
    CadProject,
    ProjectNotFoundError,
    ProjectStore,
    ProjectWorkflow,
    RevisionNotFoundError,
    SourceValidationError,
)
from cadquery_agent.rendering import DockerCadRenderer, RenderError, artifact_path
from cadquery_agent.settings import Settings, get_settings


class TextGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8_000)


class GenerationResponse(BaseModel):
    code: str
    attempts: int
    model: str


class ModelGenerationResponse(GenerationResponse):
    run_id: str
    stl_url: str
    step_url: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ConfigResponse(BaseModel):
    model: str
    server: str


class CreateProjectRequest(BaseModel):
    name: str = Field(default="Untitled design", min_length=1, max_length=80)


class ProjectStepRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=8_000)
    base_revision: int | None = Field(default=None, ge=1)


class ProjectSourceRequest(BaseModel):
    code: str = Field(min_length=1, max_length=100_000)
    base_revision: int | None = Field(default=None, ge=1)


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


class ProjectResponse(BaseModel):
    project_id: str
    name: str
    created_at: str
    revisions: list[RevisionResponse]


def get_agent(settings: Annotated[Settings, Depends(get_settings)]) -> CadQueryAgent:
    return CadQueryAgent(
        backend=OpenAICompatibleBackend(settings),
        model=settings.model,
        max_attempts=settings.max_attempts,
    )


def get_renderer(settings: Annotated[Settings, Depends(get_settings)]) -> DockerCadRenderer:
    return DockerCadRenderer(settings)


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
            )
            for revision in project.revisions
        ],
    )


app = FastAPI(title="CadQuery Studio", version="0.3.0")
_INDEX_HTML = Path(__file__).with_name("web") / "index.html"


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def interface() -> HTMLResponse:
    return HTMLResponse(_INDEX_HTML.read_text(encoding="utf-8"))


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.get("/config", response_model=ConfigResponse)
async def config(settings: Annotated[Settings, Depends(get_settings)]) -> ConfigResponse:
    return ConfigResponse(model=settings.model, server=settings.base_url)


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


@app.post("/projects/{project_id}/steps", response_model=ProjectResponse)
async def add_project_step(
    project_id: str,
    request: ProjectStepRequest,
    workflow: Annotated[ProjectWorkflow, Depends(get_project_workflow)],
) -> ProjectResponse:
    try:
        project, _ = await workflow.add_step(
            project_id=project_id,
            instruction=request.instruction,
            base_revision=request.base_revision,
        )
    except (ProjectNotFoundError, RevisionNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ModelServerError, GenerationError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except RenderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return project_response(project)


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
        )
    except (ProjectNotFoundError, RevisionNotFoundError) as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except SourceValidationError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RenderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return project_response(project)


@app.post("/generate", response_model=GenerationResponse)
async def generate_text(
    request: TextGenerationRequest,
    agent: Annotated[CadQueryAgent, Depends(get_agent)],
) -> GenerationResponse:
    try:
        result = await agent.generate(prompt=request.prompt)
    except (ModelServerError, GenerationError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return GenerationResponse(code=result.code, attempts=result.attempts, model=result.model)


async def _generate_and_render(
    agent: CadQueryAgent,
    renderer: DockerCadRenderer,
    prompt: str,
    image=None,
) -> ModelGenerationResponse:
    try:
        result = await agent.generate(prompt=prompt, image=image)
        rendered = await renderer.render(result.code)
    except (ModelServerError, GenerationError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except RenderError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    return ModelGenerationResponse(
        code=result.code,
        attempts=result.attempts,
        model=result.model,
        run_id=rendered.run_id,
        stl_url=f"/artifacts/{rendered.run_id}/model.stl",
        step_url=f"/artifacts/{rendered.run_id}/model.step",
    )


@app.post("/models/generate", response_model=ModelGenerationResponse)
async def generate_text_model(
    request: TextGenerationRequest,
    agent: Annotated[CadQueryAgent, Depends(get_agent)],
    renderer: Annotated[DockerCadRenderer, Depends(get_renderer)],
) -> ModelGenerationResponse:
    return await _generate_and_render(agent, renderer, request.prompt)


@app.post("/generate-image", response_model=GenerationResponse)
async def generate_image(
    image: Annotated[UploadFile, File()],
    agent: Annotated[CadQueryAgent, Depends(get_agent)],
    settings: Annotated[Settings, Depends(get_settings)],
    prompt: Annotated[str, Form(max_length=8_000)] = "",
) -> GenerationResponse:
    data = await image.read(settings.max_image_bytes + 1)
    try:
        validated_image = validate_image_bytes(data, settings.max_image_bytes)
        result = await agent.generate(prompt=prompt, image=validated_image)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except (ModelServerError, GenerationError) as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    return GenerationResponse(code=result.code, attempts=result.attempts, model=result.model)


@app.post("/models/generate-image", response_model=ModelGenerationResponse)
async def generate_image_model(
    image: Annotated[UploadFile, File()],
    agent: Annotated[CadQueryAgent, Depends(get_agent)],
    renderer: Annotated[DockerCadRenderer, Depends(get_renderer)],
    settings: Annotated[Settings, Depends(get_settings)],
    prompt: Annotated[str, Form(max_length=8_000)] = "",
) -> ModelGenerationResponse:
    data = await image.read(settings.max_image_bytes + 1)
    try:
        validated_image = validate_image_bytes(data, settings.max_image_bytes)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return await _generate_and_render(agent, renderer, prompt, validated_image)


@app.get("/artifacts/{run_id}/model.{extension}", include_in_schema=False)
async def download_artifact(run_id: str, extension: Literal["stl", "step"]) -> FileResponse:
    try:
        path = artifact_path(Path("artifacts/runs"), run_id, extension)
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    if not path.is_file():
        raise HTTPException(status_code=404, detail="model artifact not found")
    media_type = "model/stl" if extension == "stl" else "model/step"
    return FileResponse(path, media_type=media_type, filename=path.name)
