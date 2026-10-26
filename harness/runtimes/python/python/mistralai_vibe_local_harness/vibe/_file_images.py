"""File-backed images: an image kept with its file is a standard embedded resource.

History holds the image bytes in the resource's blob and the file in its URI.
Core gives a model that reads images the image, and a model without image
input `[Image]` with the link, so nothing here depends on the model.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from urllib.parse import urlparse

from mistralai_vibe_local_harness.protocol import (
    RustBlobResourceContents,
    RustContentAnnotations,
    RustEmbeddedResourceContentBlock,
)

_IMAGE_SUFFIX_BY_MIME_TYPE = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def file_image_content_block(
    *,
    data: str,
    uri: str,
    mime_type: str,
    annotations: RustContentAnnotations | None = None,
) -> RustEmbeddedResourceContentBlock:
    """The block for an image read from the file at `uri`, its bytes base64 in `data`."""
    return RustEmbeddedResourceContentBlock(
        resource=RustBlobResourceContents(uri=uri, mime_type=mime_type, blob=data),
        annotations=annotations,
    )


def is_file_image(block: object) -> bool:
    """Whether `block` is an embedded image resource whose URI names a file."""
    if not isinstance(block, RustEmbeddedResourceContentBlock):
        return False
    resource = block.resource
    return (
        isinstance(resource, RustBlobResourceContents)
        and (resource.mime_type or "").startswith("image/")
        and urlparse(resource.uri).scheme == "file"
    )


def save_imported_image(data: str, *, mime_type: str, attachments_root: Path) -> str:
    """Write imported image bytes into the destination session; returns the file URI."""
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError as exc:
        raise ValueError("Imported file image is not valid base64") from exc

    suffix = _IMAGE_SUFFIX_BY_MIME_TYPE.get(mime_type, ".img")
    digest = hashlib.sha256(raw).hexdigest()
    attachments_root.mkdir(parents=True, exist_ok=True)
    destination = attachments_root / f"{digest}{suffix}"
    if not destination.exists():
        destination.write_bytes(raw)
    return destination.resolve().as_uri()


__all__ = ["file_image_content_block", "is_file_image", "save_imported_image"]
