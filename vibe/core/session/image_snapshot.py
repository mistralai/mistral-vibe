from __future__ import annotations

import base64
import contextlib
import hashlib
import mimetypes
import os
from pathlib import Path
import tempfile

from vibe.core.types import FileImageSource, ImageAttachment, InlineImageSource
from vibe.utils.images import IMAGE_EXTENSIONS, MAX_IMAGE_BYTES

_DEFAULT_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


class ImageSnapshotError(Exception):
    pass


def _check_size(data: bytes) -> None:
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageSnapshotError(f"Image is too large: {len(data)} > {MAX_IMAGE_BYTES}")


def _persist(data: bytes, *, ext: str, session_dir: Path) -> Path:
    attachments_dir = session_dir / "attachments"
    attachments_dir.mkdir(parents=True, exist_ok=True)

    digest = hashlib.sha1(data, usedforsecurity=False).hexdigest()
    dest = attachments_dir / f"{digest}{ext}"
    if _holds(dest, data):
        return dest.resolve()
    # Written aside, synced, then renamed, so neither a failed write nor a
    # crash leaves a damaged image under the digest name for later reuse.
    fd, name = tempfile.mkstemp(dir=attachments_dir, prefix=f".{dest.name}.")
    tmp = Path(name)
    try:
        with open(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dest)
    except BaseException as e:
        with contextlib.suppress(OSError):
            tmp.unlink()
        # Windows refuses to replace a file another process holds open; one
        # already holding these exact bytes is as good.
        if not isinstance(e, OSError) or not _holds(dest, data):
            raise
    return dest.resolve()


def _holds(dest: Path, data: bytes) -> bool:
    try:
        return dest.read_bytes() == data
    except OSError:
        return False


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

    try:
        path = _persist(data, ext=ext, session_dir=session_dir)
    except OSError as e:
        raise ImageSnapshotError(
            f"Couldn't save it to the session: {e.strerror or e}"
        ) from e

    return ImageAttachment(
        source=FileImageSource(path=path), alias=alias, mime_type=mime_type
    )
