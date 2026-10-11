"""Saving uploaded documents to disk (the `uploads/` folder)."""

import uuid
from pathlib import Path
from typing import BinaryIO

from app.core.config import settings

# content type -> (file extension, how the file must start)
ALLOWED_TYPES: dict[str, tuple[str, tuple[bytes, ...]]] = {
    "image/jpeg": (".jpg", (b"\xff\xd8\xff",)),
    "image/png": (".png", (b"\x89PNG\r\n\x1a\n",)),
    "image/webp": (".webp", (b"RIFF",)),
    "application/pdf": (".pdf", (b"%PDF",)),
}
_CHUNK = 1024 * 1024


class UploadError(ValueError):
    pass


def save_upload(claim_id: int, source: BinaryIO, content_type: str | None) -> tuple[str, int]:
    """Validate and store a file. Returns (stored path, size in bytes)."""
    if content_type not in ALLOWED_TYPES:
        raise UploadError(f"Only JPEG, PNG, WEBP or PDF files are allowed (got {content_type}).")
    extension, signatures = ALLOWED_TYPES[content_type]

    head = source.read(12)
    if not head.startswith(signatures) or (content_type == "image/webp" and head[8:12] != b"WEBP"):
        raise UploadError("The file content does not match its type.")

    folder = settings.upload_dir / f"claim_{claim_id}"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}{extension}"

    max_bytes = settings.max_upload_mb * 1024 * 1024
    size = len(head)
    with path.open("wb") as out:
        out.write(head)
        while chunk := source.read(_CHUNK):
            size += len(chunk)
            if size > max_bytes:
                out.close()
                path.unlink(missing_ok=True)
                raise UploadError(f"File is larger than {settings.max_upload_mb} MB.")
            out.write(chunk)
    return str(path), size


def delete_file(stored_path: str) -> None:
    Path(stored_path).unlink(missing_ok=True)
