from pathlib import Path

import pytest

from cadquery_agent.rendering import DockerCadRenderer, artifact_path
from cadquery_agent.settings import Settings


def test_docker_worker_has_strict_runtime_limits(tmp_path: Path) -> None:
    renderer = DockerCadRenderer(Settings(), artifacts_root=tmp_path)
    command = renderer._command("docker", tmp_path / "input", tmp_path / "output")

    assert command[:3] == ["docker", "run", "--rm"]
    assert ["--network", "none"] == command[3:5]
    assert "--read-only" in command
    assert "no-new-privileges" in command
    assert "--memory" in command
    assert command[-1] == "cadquery-agent-worker:local"


def test_rootless_podman_keeps_host_user_for_bind_mounts(tmp_path: Path) -> None:
    renderer = DockerCadRenderer(Settings(), artifacts_root=tmp_path)
    command = renderer._command("podman", tmp_path / "input", tmp_path / "output")

    assert ["--userns", "keep-id"] == command[-3:-1]


def test_artifact_path_accepts_only_uuid_and_known_format(tmp_path: Path) -> None:
    run_id = "9f8f36ad-7dc6-4db9-80ea-b739813e7671"
    assert artifact_path(tmp_path, run_id, "stl").name == "model.stl"
    with pytest.raises(FileNotFoundError):
        artifact_path(tmp_path, "../../etc", "stl")
    with pytest.raises(FileNotFoundError):
        artifact_path(tmp_path, run_id, "py")
