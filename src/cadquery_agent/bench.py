import io
import math
import re
import time
from dataclasses import dataclass

from PIL import Image, ImageDraw

from cadquery_agent.agent import CadQueryAgent, GenerationError
from cadquery_agent.images import ImageInput
from cadquery_agent.speech import transcribe


@dataclass(frozen=True, slots=True)
class Case:
    prompt: str
    size: tuple[float, float, float]
    volume: float


# Volume checks intent: a misplaced hole or a "hole" built as a boss changes it.
CASES = (
    Case("Create a 100 x 60 x 8 mm plate.", (100, 60, 8), 48_000),
    Case(
        "A 100 x 60 x 8 mm plate with four 6 mm through holes, "
        "each hole center 10 mm from the two nearest edges.",
        (100, 60, 8),
        48_000 - 4 * math.pi * 3**2 * 8,
    ),
    Case(
        "A cylinder 40 mm in diameter and 25 mm tall with a 10 mm through hole along its axis.",
        (40, 40, 25),
        math.pi * (20**2 - 5**2) * 25,
    ),
    Case(
        "An L bracket: a 60 x 40 x 5 mm base plate and a 60 mm long, 5 mm thick, "
        "40 mm tall wall standing on top of one 60 mm edge.",
        (60, 40, 45),
        60 * 40 * 5 + 60 * 5 * 40,
    ),
    Case(
        "A 50 mm cube with every edge rounded with a 5 mm fillet.",
        (50, 50, 50),
        40**3 + 6 * 5 * 40**2 + 3 * math.pi * 5**2 * 40 + 4 / 3 * math.pi * 5**3,
    ),
)


@dataclass(frozen=True, slots=True)
class Score:
    case: Case
    passed: bool
    attempts: int
    seconds: float
    note: str


def matches(case: Case, stats: dict) -> str:
    size = sorted(stats.get("size", ()))
    if len(size) != 3 or any(abs(a - b) > 0.5 for a, b in zip(size, sorted(case.size))):
        return f"size {stats.get('size')} != {list(case.size)}"
    if abs(stats.get("volume", 0) - case.volume) > 0.02 * case.volume:
        return f"volume {stats.get('volume', 0):.0f} != {case.volume:.0f}"
    return ""


async def run_case(agent: CadQueryAgent, case: Case) -> Score:
    started = time.perf_counter()
    try:
        result = await agent.generate(prompt=case.prompt)
    except GenerationError as error:
        return Score(case, False, agent.max_attempts, time.perf_counter() - started, str(error))
    note = matches(case, result.render.stats if result.render else {})
    return Score(case, not note, result.attempts, time.perf_counter() - started, note or "ok")


SKETCH_NUMBERS = ("80", "50", "5", "20")
SKETCH_ABSENT = ("boss", "slot", "fillet", "chamfer")  # not drawn, so must not be described


def plate_sketch() -> ImageInput:
    """A labelled drawing of an 80 x 50 x 5 mm plate with a 20 mm hole."""
    picture = Image.new("RGB", (640, 440), "white")
    pen = ImageDraw.Draw(picture)
    pen.rectangle((120, 120, 520, 320), outline="black", width=4)
    pen.ellipse((270, 170, 370, 270), outline="black", width=4)
    pen.line((120, 360, 520, 360), fill="black", width=2)
    pen.line((560, 120, 560, 320), fill="black", width=2)
    for xy, label in (
        ((295, 370), "80 mm"),
        ((570, 212), "50 mm"),
        ((380, 160), "hole 20"),
        ((140, 130), "5 mm thick"),
    ):
        pen.text(xy, label, fill="black", font_size=18)
    buffer = io.BytesIO()
    picture.save(buffer, format="PNG")
    return ImageInput(data=buffer.getvalue(), media_type="image/png")


async def run_vision(agent: CadQueryAgent, vision_model: str) -> tuple[bool, float, str]:
    started = time.perf_counter()
    text = await agent.describe(plate_sketch(), vision_model)
    found = [number for number in SKETCH_NUMBERS if re.search(rf"(?<!\d){number}(?!\d)", text)]
    invented = [word for word in SKETCH_ABSENT if word in text.lower()]
    passed = len(found) == len(SKETCH_NUMBERS) and not invented
    return passed, time.perf_counter() - started, text


def run_voice(audio: bytes, voice_model: str) -> tuple[float, str]:
    started = time.perf_counter()
    text = transcribe(audio, voice_model)
    return time.perf_counter() - started, text
