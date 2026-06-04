from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path

from vibe.core.types import IMAGE_EXTENSIONS, ImageAttachment

_DEFAULT_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class ImageSnapshotError(Exception):
    pass


def snapshot_image(
    source: Path, *, alias: str, session_dir: Path | None
) -> ImageAttachment:
    source_abs = source.expanduser().resolve()
    if not source_abs.is_file():
        raise ImageSnapshotError(f"Not a file: {source_abs}")

    ext = source_abs.suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ImageSnapshotError(f"Unsupported image extension: {ext}")

    mime_type = _DEFAULT_MIME_BY_EXT.get(ext) or (
        mimetypes.guess_type(source_abs.name)[0] or "application/octet-stream"
    )

    try:
        data = source_abs.read_bytes()
    except OSError as e:
        raise ImageSnapshotError(f"Failed to read image {source_abs}: {e}") from e

    # Session logging disabled: no snapshot copy is made; the attachment
    # points to the original source. Resume is not possible in this mode so
    # snapshot stability is not required.
    if session_dir is None:
        return ImageAttachment(path=source_abs, alias=alias, mime_type=mime_type)

    attachments_dir = session_dir / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha1(data, usedforsecurity=False).hexdigest()
    dest = attachments_dir / f"{digest}{ext}"
    if not dest.exists():
        dest.write_bytes(data)

    return ImageAttachment(path=dest.resolve(), alias=alias, mime_type=mime_type)
