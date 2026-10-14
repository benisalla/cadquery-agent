from fastapi.testclient import TestClient

from cadquery_agent.agent import GenerationResult
from cadquery_agent.api import app, get_agent


class FakeAgent:
    async def generate(self, prompt: str = "", image=None) -> GenerationResult:
        return GenerationResult(
            code="import cadquery as cq\nresult = cq.Workplane('XY').box(1, 2, 3)",
            attempts=1,
            model="qwen2.5vl:3b",
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
    assert "CadQuery Agent" in response.text
    assert "Generate CadQuery" in response.text


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
