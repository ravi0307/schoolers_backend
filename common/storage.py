"""Shared image upload helpers — local disk in dev, same contract for S3 later."""
import re
from datetime import datetime, timezone
from pathlib import Path

from common.config import settings

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
}

EXTENSION_TO_MEDIA_TYPE = {ext: mime for mime, ext in ALLOWED_IMAGE_TYPES.items()}


def get_upload_dir() -> Path:
    """Return the configured upload directory, creating it if needed."""
    if settings.UPLOAD_BACKEND != "local":
        raise NotImplementedError(
            f"Upload backend '{settings.UPLOAD_BACKEND}' is not configured"
        )
    directory = Path(settings.UPLOAD_LOCAL_PATH)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def save_image(
    file_bytes: bytes,
    content_type: str,
    filename_prefix: str | None = None,
) -> str:
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise ValueError("Only JPEG, PNG, GIF, WebP, and SVG images are allowed")
    if len(file_bytes) > settings.UPLOAD_MAX_BYTES:
        raise ValueError("Image must be 5 MB or smaller")

    extension = ALLOWED_IMAGE_TYPES[content_type]
    if filename_prefix:
        safe_prefix = re.sub(r"[^A-Za-z0-9]+", "_", filename_prefix).strip("_")
        if not safe_prefix:
            safe_prefix = "school"
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{safe_prefix}_{timestamp}{extension}"
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        filename = f"image_{timestamp}{extension}"
    (get_upload_dir() / filename).write_bytes(file_bytes)
    return filename


def resolve_upload_path(filename: str) -> Path:
    if ".." in filename or "/" in filename or "\\" in filename:
        raise FileNotFoundError(filename)

    path = get_upload_dir() / filename
    if not path.is_file():
        raise FileNotFoundError(filename)
    return path


def media_type_for_filename(filename: str) -> str | None:
    return EXTENSION_TO_MEDIA_TYPE.get(Path(filename).suffix.lower())
