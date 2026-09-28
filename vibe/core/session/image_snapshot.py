from __future__ import annotations

import base64
import hashlib
import mimetypes
from pathlib import Path

from vibe.core.types import FileImageSource, ImageAttachment, InlineImageSource
from vibe.utils.images import IMAGE_EXTENSIONS, MAX_IMAGE_BYTES

_DEFAULT_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}

_EXT_BY_MIME = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


class ImageSnapshotError(Exception):
    pass


def extension_for_mime(mime_type: str) -> str | None:
    return _EXT_BY_MIME.get(mime_type)


def _check_size(data: bytes) -> None:
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageSnapshotError(f"Image is too large: {len(data)} > {MAX_IMAGE_BYTES}")


def _persist(data: bytes, *, ext: str, session_dir: Path) -> Path:
    attachments_dir = session_dir / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha1(data, usedforsecurity=False).hexdigest()
    dest = attachments_dir / f"{digest}{ext}"
    if not dest.exists():
        dest.write_bytes(data)
    return dest.resolve()


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

    _check_size(data)

    # No session dir means the attachment cannot be persisted next to the
    # session transcript. Keep the bytes inline: a file source would point
    # outside the workspace or session attachments and be rejected by turn
    # input validation.
    if session_dir is None:
        return ImageAttachment(
            source=InlineImageSource(data=base64.b64encode(data).decode("ascii")),
            alias=alias,
            mime_type=mime_type,
        )

    return ImageAttachment(
        source=FileImageSource(path=_persist(data, ext=ext, session_dir=session_dir)),
        alias=alias,
        mime_type=mime_type,
    )


def snapshot_image_bytes(
    data: bytes, *, alias: str, mime_type: str, session_dir: Path | None
) -> ImageAttachment:
    ext = extension_for_mime(mime_type)
    if ext is None:
        raise ImageSnapshotError(f"Unsupported image mime type: {mime_type}")

    _check_size(data)

    # No session dir means logging is disabled and nothing should be written to
    # disk: keep the bytes inline so the attachment outlives this call without a
    # dangling file path.
    if session_dir is None:
        return ImageAttachment(
            source=InlineImageSource(data=base64.b64encode(data).decode("ascii")),
            alias=alias,
            mime_type=mime_type,
        )

    return ImageAttachment(
        source=FileImageSource(path=_persist(data, ext=ext, session_dir=session_dir)),
        alias=alias,
        mime_type=mime_type,
    )
