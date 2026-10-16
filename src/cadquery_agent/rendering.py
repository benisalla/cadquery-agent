import asyncio
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

from cadquery_agent.settings import Settings

_IMAGE_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:/-]{0,199}$")


_detected_runtime: str | None = None


class RenderError(RuntimeError):
    """A validated script could not be rendered in the isolated worker."""


class WorkerUnavailableError(RenderError):
    """No container runtime can run the worker, so retrying the script cannot help."""


@dataclass(frozen=True, slots=True)
class RenderResult:
    run_id: str
    stl_path: Path
    step_path: Path
    stats: dict = field(default_factory=dict)


class DockerCadRenderer:
    def __init__(self, settings: Settings, artifacts_root: Path | None = None) -> None:
        self.image = settings.worker_image
        self.timeout_seconds = settings.render_timeout_seconds
        self.artifacts_root = (artifacts_root or Path("artifacts/runs")).resolve()
        if not _IMAGE_PATTERN.fullmatch(self.image):
            raise ValueError("worker_image contains unsupported characters")

    async def render(self, source: str) -> RenderResult:
        runtime = await self._select_runtime()

        run_id = str(uuid4())
        run_dir = self.artifacts_root / run_id
        input_dir = run_dir / "input"
        output_dir = run_dir / "output"
        input_dir.mkdir(parents=True, mode=0o700)
        output_dir.mkdir(mode=0o700)
        (input_dir / "model.py").write_text(source, encoding="utf-8")

        command = self._command(runtime, input_dir, output_dir)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError as error:
            process.kill()
            await process.communicate()
            raise RenderError(f"CAD rendering exceeded {self.timeout_seconds:g} seconds") from error

        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            if not detail:
                detail = stdout.decode("utf-8", errors="replace").strip()
            raise RenderError(f"CAD worker failed: {detail[-800:] or 'unknown error'}")

        stl_path = output_dir / "model.stl"
        step_path = output_dir / "model.step"
        if not stl_path.is_file() or not step_path.is_file():
            raise RenderError("CAD worker did not produce both STL and STEP files")
        stats_path = output_dir / "stats.json"
        stats = json.loads(stats_path.read_text()) if stats_path.is_file() else {}
        return RenderResult(run_id=run_id, stl_path=stl_path, step_path=step_path, stats=stats)

    async def _select_runtime(self) -> str:
        # Detect once: a busy machine can make `info` slow, which must not look like "no runtime".
        global _detected_runtime
        if _detected_runtime is None:
            _detected_runtime = await self._detect_runtime()
        return _detected_runtime

    async def _detect_runtime(self) -> str:
        for candidate in ("docker", "podman"):
            if shutil.which(candidate) is None:
                continue
            process = await asyncio.create_subprocess_exec(
                candidate,
                "info",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                return_code = await asyncio.wait_for(process.wait(), timeout=20)
            except TimeoutError:
                process.kill()
                await process.wait()
                continue
            if return_code == 0:
                return candidate
        raise WorkerUnavailableError(
            "No working container runtime was found. Start Docker or install rootless Podman, "
            "then run './scripts/build_worker.sh'."
        )

    def _command(self, runtime: str, input_dir: Path, output_dir: Path) -> list[str]:
        command = [
            runtime,
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
            "--memory",
            "2g",
            "--cpus",
            "2",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env",
            "HOME=/tmp",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,src={input_dir},dst=/input,readonly",
            "--mount",
            f"type=bind,src={output_dir},dst=/output",
        ]
        if runtime == "podman":
            command.extend(["--userns", "keep-id"])
        command.append(self.image)
        return command


def artifact_path(artifacts_root: Path, run_id: str, extension: str) -> Path:
    try:
        safe_id = str(UUID(run_id))
    except ValueError as error:
        raise FileNotFoundError("unknown model") from error
    if extension not in {"stl", "step"}:
        raise FileNotFoundError("unknown artifact type")
    return artifacts_root.resolve() / safe_id / "output" / f"model.{extension}"
