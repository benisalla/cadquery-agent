from fastapi.testclient import TestClient

from cadquery_agent.agent import GenerationResult
from cadquery_agent.api import (
    app,
    get_agent,
    get_project_store,
    get_project_workflow,
    get_renderer,
)
from cadquery_agent.projects import ProjectStore, ProjectWorkflow
from cadquery_agent.rendering import RenderResult


class FakeAgent:
    async def generate(
        self,
        prompt: str = "",
        image=None,
        existing_source: str | None = None,
    ) -> GenerationResult:
        del image, existing_source
        return GenerationResult(
            code="import cadquery as cq\nresult = cq.Workplane('XY').box(1, 2, 3)",
            attempts=1,
            model="qwen2.5vl:3b",
        )


class FakeRenderer:
    async def render(self, source: str) -> RenderResult:
        assert "Workplane" in source
        return RenderResult(
            run_id="9f8f36ad-7dc6-4db9-80ea-b739813e7671",
            stl_path=None,  # type: ignore[arg-type]
            step_path=None,  # type: ignore[arg-type]
        )


def test_health_does_not_contact_a_model() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_interface_is_available() -> None:
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "CadQuery Studio" in response.text
    assert "Build next step" in response.text
    assert "STLLoader" in response.text


def test_generate_returns_validated_source() -> None:
    app.dependency_overrides[get_agent] = lambda: FakeAgent()
    try:
        with TestClient(app) as client:
            response = client.post("/generate", json={"prompt": "a 30 mm cube"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["model"] == "qwen2.5vl:3b"
    assert "Workplane" in response.json()["code"]


def test_generate_model_returns_preview_and_download_urls() -> None:
    app.dependency_overrides[get_agent] = lambda: FakeAgent()
    app.dependency_overrides[get_renderer] = lambda: FakeRenderer()
    try:
        with TestClient(app) as client:
            response = client.post("/models/generate", json={"prompt": "a 30 mm cube"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    body = response.json()
    assert body["stl_url"].endswith("/model.stl")
    assert body["step_url"].endswith("/model.step")


def test_project_api_creates_and_appends_a_build_step(tmp_path) -> None:
    store = ProjectStore(tmp_path / "projects")
    workflow = ProjectWorkflow(store, FakeAgent(), FakeRenderer())
    app.dependency_overrides[get_project_store] = lambda: store
    app.dependency_overrides[get_project_workflow] = lambda: workflow
    try:
        with TestClient(app) as client:
            created = client.post("/projects", json={"name": "Robot arm"})
            project_id = created.json()["project_id"]
            stepped = client.post(
                f"/projects/{project_id}/steps",
                json={"instruction": "Create the mounting base"},
            )
    finally:
        app.dependency_overrides.clear()
    assert created.status_code == 201
    assert stepped.status_code == 200
    assert stepped.json()["name"] == "Robot arm"
    assert stepped.json()["revisions"][0]["instruction"] == "Create the mounting base"
    assert stepped.json()["revisions"][0]["stl_url"].endswith("model.stl")
