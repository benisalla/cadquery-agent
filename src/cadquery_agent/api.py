from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.responses import HTMLResponse

from cadquery_agent.agent import CadQueryAgent, GenerationError
from cadquery_agent.backend import ModelServerError, OpenAICompatibleBackend
from cadquery_agent.images import validate_image_bytes
from cadquery_agent.settings import Settings, get_settings


class TextGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=8_000)


class GenerationResponse(BaseModel):
    code: str
    attempts: int
    model: str


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ConfigResponse(BaseModel):
    model: str
    server: str


def get_agent(settings: Annotated[Settings, Depends(get_settings)]) -> CadQueryAgent:
    return CadQueryAgent(
        backend=OpenAICompatibleBackend(settings),
        model=settings.model,
        max_attempts=settings.max_attempts,
    )


app = FastAPI(title="CADQuery Agent", version="0.1.0")
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
