from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelProfile:
    """How to talk to one model family; matched by model-name prefix."""

    prefix: str
    temperature: float = 0.2
    top_p: float = 0.9
    vision: bool = False
    no_think: bool = False


# Sampling values follow each model card; code-writing runs a little cooler.
# Order matters: the first matching prefix wins.
PROFILES = (
    ModelProfile("qwen2.5-coder", temperature=0.2, top_p=0.8),
    ModelProfile("qwen2.5vl", temperature=0.1, top_p=0.8, vision=True),
    ModelProfile("qwen3.5", temperature=0.6, top_p=0.8, vision=True, no_think=True),
    ModelProfile("qwen3-vl", temperature=0.6, top_p=0.8, vision=True, no_think=True),
    ModelProfile("qwen3", temperature=0.6, top_p=0.8, no_think=True),
    ModelProfile("gemma3", temperature=0.3, top_p=0.95, vision=True),
    ModelProfile("gemma-3", temperature=0.3, top_p=0.95, vision=True),
    ModelProfile("ministral-3", temperature=0.1, vision=True),
    ModelProfile("llama3.2-vision", vision=True),
    ModelProfile("minicpm-v", vision=True),
    ModelProfile("llava", vision=True),
)
DEFAULT_PROFILE = ModelProfile("")


def profile_for(model: str) -> ModelProfile:
    name = model.lower().rsplit("/", 1)[-1]
    return next(
        (profile for profile in PROFILES if name.startswith(profile.prefix)), DEFAULT_PROFILE
    )
