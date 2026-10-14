from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SupportedModel:
    name: str
    input_types: tuple[str, ...]
    approximate_download_gb: float
    note: str


SUPPORTED_MODELS: dict[str, SupportedModel] = {
    "qwen2.5vl:3b": SupportedModel(
        name="qwen2.5vl:3b",
        input_types=("text", "image"),
        approximate_download_gb=3.2,
        note="Optional image-capable model; needs substantially more free memory.",
    ),
    "qwen2.5-coder:1.5b": SupportedModel(
        name="qwen2.5-coder:1.5b",
        input_types=("text",),
        approximate_download_gb=1.0,
        note="Recommended reliable text-to-CadQuery model for this machine.",
    ),
    "qwen2.5-coder:3b": SupportedModel(
        name="qwen2.5-coder:3b",
        input_types=("text",),
        approximate_download_gb=1.9,
        note="Better text-to-code quality while remaining practical on CPU.",
    ),
    "Qwen/Qwen2.5-VL-3B-Instruct": SupportedModel(
        name="Qwen/Qwen2.5-VL-3B-Instruct",
        input_types=("text", "image"),
        approximate_download_gb=7.0,
        note="Model identifier for a local vLLM server.",
    ),
}

DEFAULT_MODEL = "qwen2.5-coder:1.5b"


def require_supported_model(model: str, has_image: bool = False) -> SupportedModel:
    try:
        selected = SUPPORTED_MODELS[model]
    except KeyError as error:
        choices = ", ".join(sorted(SUPPORTED_MODELS))
        raise ValueError(f"Unsupported model {model!r}. Choose one of: {choices}") from error
    if has_image and "image" not in selected.input_types:
        raise ValueError(f"Model {model!r} is text-only; select qwen2.5vl:3b for images")
    return selected
