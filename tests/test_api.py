import io
import json

from fastapi.testclient import TestClient
from PIL import Image

from cadquery_agent.agent import Attempt, GenerationError, GenerationResult
from cadquery_agent.api import app, get_project_store, get_project_workflow, get_renderer
from cadquery_agent.projects import ProjectStore, ProjectWorkflow
from cadquery_agent.rendering import RenderResult
from cadquery_agent.settings import get_settings

BOX = "import cadquery as cq\nresult = cq.Workplane('XY').box(1, 2, 3)"


def fake_render() -> RenderResult:
    return RenderResult(
        run_id="9f8f36ad-7dc6-4db9-80ea-b739813e7671",
        stl_path=None,  # type: ignore[arg-type]
        step_path=None,  # type: ignore[arg-type]
        stats={"size": [1.0, 2.0, 3.0], "volume": 6.0, "valid": True},
    )


class FakeAgent:
    def __init__(self) -> None:
        self.images: list[object] = []

    async def generate(
        self,
        prompt: str = "",
        image=None,
        existing_source: str | None = None,
        existing_size=None,
        model: str | None = None,
        vision_model: str | None = None,
        on_event=None,
    ) -> GenerationResult:
        self.images.append(image)
        if on_event is not None:
            on_event({"event": "stage", "stage": "write", "attempt": 1, "max_attempts": 1})
        if prompt == "fail":
            raise GenerationError("model did not produce a working script: boom")
        description = "A plate" if image is not None else None
        return GenerationResult(
            BOX, "qwen2.5-coder:1.5b", (Attempt(BOX, ()),), fake_render(), description
        )


class FakeRenderer:
    async def render(self, source: str) -> RenderResult:
        assert "Workplane" in source
        return fake_render()


def project_client(tmp_path, agent: FakeAgent) -> TestClient:
    store = ProjectStore(tmp_path / "projects")
    workflow = ProjectWorkflow(store, agent, FakeRenderer())
    app.dependency_overrides[get_project_store] = lambda: store
    app.dependency_overrides[get_project_workflow] = lambda: workflow
    return TestClient(app)


def png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_health_does_not_contact_a_model() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_interface_is_available() -> None:
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Run code" in response.text
    assert "STLLoader" in response.text


def test_config_reports_models_and_voice() -> None:
    with TestClient(app) as client:
        body = client.get("/config").json()
    assert body["model"] == get_settings().model
    assert body["vision_model"] == get_settings().vision_model
    assert isinstance(body["voice"], bool)


def test_steps_accept_a_multipart_instruction(tmp_path) -> None:
    agent = FakeAgent()
    try:
        with project_client(tmp_path, agent) as client:
            created = client.post("/projects", json={"name": "Robot arm"})
            project_id = created.json()["project_id"]
            stepped = client.post(
                f"/projects/{project_id}/steps",
                data={"instruction": "Create the mounting base"},
            )
    finally:
        app.dependency_overrides.clear()
    assert created.status_code == 201
    assert stepped.status_code == 200
    revision = stepped.json()["revisions"][0]
    assert revision["instruction"] == "Create the mounting base"
    assert revision["stl_url"].endswith("model.stl")
    assert revision["size"] == [1.0, 2.0, 3.0]
    assert revision["trace"] == [[]]
    assert agent.images == [None]


def test_steps_accept_a_sketch_image(tmp_path) -> None:
    agent = FakeAgent()
    try:
        with project_client(tmp_path, agent) as client:
            project_id = client.post("/projects", json={}).json()["project_id"]
            stepped = client.post(
                f"/projects/{project_id}/steps",
                files={"image": ("sketch.png", png_bytes(), "image/png")},
            )
            empty = client.post(f"/projects/{project_id}/steps", data={"instruction": " "})
            bad_image = client.post(
                f"/projects/{project_id}/steps",
                files={"image": ("sketch.png", b"not an image", "image/png")},
            )
    finally:
        app.dependency_overrides.clear()
    assert stepped.status_code == 200
    assert stepped.json()["revisions"][0]["instruction"] == "Sketch: A plate"
    assert agent.images[0].media_type == "image/png"
    assert empty.status_code == 422
    assert bad_image.status_code == 422


def test_project_api_runs_manually_edited_source(tmp_path) -> None:
    source = "import cadquery as cq\nsize = 25\nresult = cq.Workplane('XY').box(size, 10, 5)"
    try:
        with project_client(tmp_path, FakeAgent()) as client:
            project_id = client.post("/projects", json={"name": "Code edit"}).json()["project_id"]
            response = client.post(f"/projects/{project_id}/source", json={"code": source})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    revision = response.json()["revisions"][0]
    assert revision["code"] == source
    assert revision["model"] == "manual"


def test_step_stream_reports_stages_then_the_project(tmp_path) -> None:
    try:
        with project_client(tmp_path, FakeAgent()) as client:
            project_id = client.post("/projects", json={}).json()["project_id"]
            response = client.post(
                f"/projects/{project_id}/steps/stream", data={"instruction": "a box"}
            )
    finally:
        app.dependency_overrides.clear()
    events = [json.loads(line) for line in response.text.splitlines()]
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert [event["event"] for event in events] == ["stage", "done"]
    assert events[1]["project"]["revisions"][0]["instruction"] == "a box"


def test_step_stream_reports_failures_as_an_error_event(tmp_path) -> None:
    try:
        with project_client(tmp_path, FakeAgent()) as client:
            project_id = client.post("/projects", json={}).json()["project_id"]
            response = client.post(
                f"/projects/{project_id}/steps/stream", data={"instruction": "fail"}
            )
    finally:
        app.dependency_overrides.clear()
    last = json.loads(response.text.splitlines()[-1])
    assert last == {"event": "error", "status": 502, "detail": last["detail"]}
    assert "boom" in last["detail"]


def test_preview_renders_without_saving_a_revision(tmp_path) -> None:
    app.dependency_overrides[get_renderer] = lambda: FakeRenderer()
    try:
        with TestClient(app) as client:
            ok = client.post("/preview", json={"code": BOX})
            bad = client.post("/preview", json={"code": "import os"})
    finally:
        app.dependency_overrides.clear()
    assert ok.status_code == 200
    assert ok.json()["size"] == [1.0, 2.0, 3.0]
    assert ok.json()["stl_url"].endswith("/model.stl")
    assert bad.status_code == 422
