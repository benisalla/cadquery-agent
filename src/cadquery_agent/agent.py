from collections.abc import Callable
from dataclasses import dataclass

from cadquery_agent.backend import ModelBackend, user_message
from cadquery_agent.images import ImageInput, shrink_image
from cadquery_agent.rendering import (
    DockerCadRenderer,
    RenderError,
    RenderResult,
    WorkerUnavailableError,
)
from cadquery_agent.validation import (
    check_geometry,
    ensure_result,
    extract_python_code,
    strip_thinking,
    validate_cadquery_source,
)

SYSTEM_PROMPT = """You write CadQuery Python scripts. Reply with one complete script in a \
```python block.

Rules:
- Model exactly what is requested, with the requested dimensions. Add nothing else.
- Use only `import cadquery as cq` and optionally `import numpy as np`.
- Put every dimension in a named variable, in millimetres.
- Start geometry with cq.Workplane('XY') and assign the final solid to `result`.
- Never read or write files, use the network, or call show(), show_object() or export().

CadQuery facts small models get wrong:
- box(x, y, z) is centered on the origin.
- circle(radius) takes a radius, while hole(diameter) takes only a diameter and cuts through.
- Make holes with hole(diameter); never extrude a circle for a hole, that adds material.
- For cylinders use circle(radius).extrude(height).
- After faces('>Z').workplane(), coordinates are relative to the face center.
- Place corner features with rect(w, h, forConstruction=True).vertices().
- Combine bodies with .union(other) and remove material with .cut(other).
- Round vertical edges with .edges('|Z').fillet(r).

Example (style only): a 40 mm disc, 6 mm thick, with a 12 mm tall 16 mm boss and four 4 mm
holes on a 24 mm square.
```python
import cadquery as cq

disc_diameter = 40
disc_thickness = 6
boss_diameter = 16
boss_height = 12
hole_diameter = 4
hole_square = 24

disc = cq.Workplane('XY').circle(disc_diameter / 2).extrude(disc_thickness)
boss = disc.faces('>Z').workplane().circle(boss_diameter / 2).extrude(boss_height)
result = (
    boss.faces('<Z')
    .workplane()
    .rect(hole_square, hole_square, forConstruction=True)
    .vertices()
    .hole(hole_diameter)
)
```"""

SKETCH_PROMPT = """This image is a hand-drawn sketch or photo of a part to model in CAD.
Describe it for a CAD engineer, in millimetres:
1. Base shape with its length, width and thickness.
2. Each feature that is actually drawn, with its size and position from the center.
List nothing that is not in the drawing.
A number next to a dimension line is the length of the edge that line runs along.
If a size is not written, estimate it from the proportions. Reply with the list only."""


Events = Callable[[dict], None]


class GenerationError(RuntimeError):
    """The model did not produce a script that passes every check."""


@dataclass(frozen=True, slots=True)
class Attempt:
    code: str
    problems: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GenerationResult:
    code: str
    model: str
    trace: tuple[Attempt, ...]
    render: RenderResult | None = None
    sketch_description: str | None = None

    @property
    def attempts(self) -> int:
        return len(self.trace)


async def check_script(
    code: str, renderer: DockerCadRenderer | None
) -> tuple[tuple[str, ...], RenderResult | None]:
    """Static checks, then a sandboxed render, then a geometry check; returns the problems."""
    problems = validate_cadquery_source(code).errors
    if problems or renderer is None:
        return problems, None
    try:
        render = await renderer.render(code)
    except WorkerUnavailableError:
        raise
    except RenderError as error:
        return (str(error),), None
    return check_geometry(render.stats), render


class CadQueryAgent:
    def __init__(
        self,
        backend: ModelBackend,
        model: str,
        vision_model: str | None = None,
        renderer: DockerCadRenderer | None = None,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self.backend = backend
        self.model = model
        self.vision_model = vision_model
        self.renderer = renderer
        self.max_attempts = max_attempts

    async def generate(
        self,
        prompt: str = "",
        image: ImageInput | None = None,
        existing_source: str | None = None,
        model: str | None = None,
        vision_model: str | None = None,
        on_event: Events | None = None,
    ) -> GenerationResult:
        emit = on_event or (lambda event: None)
        prompt = prompt.strip()
        if not prompt and image is None:
            raise ValueError("provide a text prompt, an image, or both")
        model = model or self.model
        sketch_description = None
        if image is not None:
            emit({"event": "stage", "stage": "describe"})
            sketch_description = await self.describe(image, vision_model or self.vision_model)
        task = _task(prompt, sketch_description, existing_source)

        # Only the latest failed attempt is kept, so small context windows never overflow.
        conversation = [{"role": "system", "content": SYSTEM_PROMPT}, user_message(task)]
        messages = conversation
        trace: list[Attempt] = []
        for attempt in range(1, self.max_attempts + 1):
            step = {"attempt": attempt, "max_attempts": self.max_attempts}
            emit({"event": "stage", "stage": "write", **step})
            reply = await self.backend.chat(messages, model)
            code = ensure_result(extract_python_code(reply))
            emit({"event": "stage", "stage": "verify", **step})
            problems, render = await check_script(code, self.renderer)
            trace.append(Attempt(code, problems))
            if not problems:
                return GenerationResult(code, model, tuple(trace), render, sketch_description)
            emit({"event": "retry", "problems": list(problems), **step})
            feedback = [{"role": "assistant", "content": reply}, user_message(_feedback(problems))]
            messages = conversation + feedback
        raise GenerationError("model did not produce a working script: " + "; ".join(problems))

    async def describe(self, image: ImageInput, vision_model: str | None) -> str:
        if vision_model is None:
            raise ValueError("image input needs a vision model")
        message = user_message(SKETCH_PROMPT, shrink_image(image))
        reply = await self.backend.chat([message], vision_model)
        return strip_thinking(reply)


def _task(prompt: str, sketch_description: str | None, existing_source: str | None) -> str:
    parts = []
    if existing_source is not None:
        parts.append(f"Current script:\n```python\n{existing_source}\n```")
    if sketch_description:
        parts.append(f"Sketch description:\n{sketch_description}")
    if prompt and existing_source is not None:
        parts.append(f"Change request: {prompt}")
    elif prompt and sketch_description:
        parts.append(f"Request (it overrides the sketch description): {prompt}")
    elif prompt:
        parts.append(prompt)
    if existing_source is not None:
        parts.append(
            "Return the complete updated script; "
            "keep every existing feature unless the request changes it."
        )
    elif not prompt:
        parts.append("Model the object from the sketch description.")
    return "\n\n".join(parts)


def _feedback(problems: tuple[str, ...]) -> str:
    listed = "\n".join(f"- {problem}" for problem in problems)
    return f"The script failed these checks:\n{listed}\nReturn the complete corrected script."
