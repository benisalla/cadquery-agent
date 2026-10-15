from dataclasses import dataclass

from cadquery_agent.backend import ModelBackend
from cadquery_agent.images import ImageInput
from cadquery_agent.models import require_supported_model
from cadquery_agent.validation import extract_python_code, validate_cadquery_source

SYSTEM_TASK = """Write editable Python source using CadQuery.
Return only Python code. Use exactly 'import cadquery as cq' and optionally
'import numpy as np'; never use from-imports or wildcard imports. Start geometry
with cq.Workplane('XY') and assign the final CadQuery object to result. A valid
20 mm cube is: result = cq.Workplane('XY').box(20, 20, 20). Do not read files,
access the network, spawn processes, or use dynamic Python execution. Prefer
explicit millimeter dimensions and simple, editable construction steps. Do not
call show(), show_object(), export(), or exporters; the worker handles preview
and file export after the final result assignment. Create primitives only
through cq.Workplane methods; never call cq.Box or cq.Cylinder. For an additive
boss use base.faces('>Z').workplane().circle(radius).extrude(height). CadQuery
circle() takes a radius, never a diameter: define boss_diameter and pass
boss_diameter / 2. For hole
arrays use faces('>Z').workplane().pushPoints(points).hole(diameter)."""

REVISION_TASK = """Revise an existing CadQuery design using the user's next instruction.
Return the complete replacement Python script, not a patch or explanation. Preserve every
existing feature unless the instruction explicitly changes or removes it. Keep dimensions in
named variables and give new bodies or features descriptive variable names so later revisions
remain easy to edit. Combine the final geometry into result. The replacement must follow all
rules below.

"""


class GenerationError(RuntimeError):
    """The model did not produce acceptable CadQuery source."""


@dataclass(frozen=True, slots=True)
class GenerationResult:
    code: str
    attempts: int
    model: str


class CadQueryAgent:
    def __init__(self, backend: ModelBackend, model: str, max_attempts: int = 3) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self.backend = backend
        self.model = model
        self.max_attempts = max_attempts

    async def generate(
        self,
        prompt: str = "",
        image: ImageInput | None = None,
        existing_source: str | None = None,
    ) -> GenerationResult:
        clean_prompt = prompt.strip()
        if not clean_prompt and image is None:
            raise ValueError("provide a text prompt, an image, or both")
        require_supported_model(self.model, has_image=image is not None)
        request = SYSTEM_TASK
        if existing_source is not None:
            request = (
                REVISION_TASK
                + SYSTEM_TASK
                + "\n\nExisting validated script:\n--- BEGIN SCRIPT ---\n"
                + existing_source
                + "\n--- END SCRIPT ---"
            )
        if clean_prompt:
            label = "Requested revision" if existing_source is not None else "User request"
            request += f"\n\n{label}: {clean_prompt}"
        elif image is not None:
            request += "\n\nReconstruct the object shown in the reference image."

        last_errors: tuple[str, ...] = ()
        for attempt in range(1, self.max_attempts + 1):
            response = await self.backend.generate(prompt=request, image=image)
            code = extract_python_code(response)
            validation = validate_cadquery_source(code)
            if validation.is_valid:
                return GenerationResult(code=code, attempts=attempt, model=self.model)
            last_errors = validation.errors
            request += (
                "\n\nThe previous response failed static checks. Return a corrected full script. "
                + "Problems: "
                + "; ".join(last_errors)
            )

        details = "; ".join(last_errors) or "unknown validation failure"
        raise GenerationError(f"model did not produce valid safe source: {details}")

    async def repair_runtime_failure(
        self,
        source: str,
        error: str,
        instruction: str,
    ) -> GenerationResult:
        prompt = (
            "Correct the script so CadQuery can execute it. Preserve the intended design. "
            f"Original design instruction: {instruction}\n"
            f"Worker error: {error[-600:]}"
        )
        return await self.generate(prompt=prompt, existing_source=source)
