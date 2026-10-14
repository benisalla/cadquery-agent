import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, UnidentifiedImageError

_ALLOWED_FORMATS = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
}


@dataclass(frozen=True, slots=True)
class ImageInput:
    data: bytes
    media_type: str


def validate_image_bytes(data: bytes, max_bytes: int) -> ImageInput:
    if not data:
        raise ValueError("image is empty")
    if len(data) > max_bytes:
        raise ValueError(f"image exceeds the {max_bytes}-byte limit")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
            media_type = _ALLOWED_FORMATS.get(image.format or "")
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError("image is not a valid PNG, JPEG, or WebP file") from error
    if media_type is None:
        raise ValueError("only PNG, JPEG, and WebP images are supported")
    return ImageInput(data=data, media_type=media_type)


def load_image(path: Path, max_bytes: int) -> ImageInput:
    if not path.is_file():
        raise FileNotFoundError(f"image does not exist: {path}")
    return validate_image_bytes(path.read_bytes(), max_bytes=max_bytes)
