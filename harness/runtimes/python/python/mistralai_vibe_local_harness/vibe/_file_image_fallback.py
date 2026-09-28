"""Attach and transfer resource-link fallbacks for file-backed images."""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from urllib.parse import urlparse

from pydantic import JsonValue, ValidationError

from mistralai_vibe_local_harness.protocol import (
    RustImageContentBlock,
    RustResourceLinkContentBlock,
)

FILE_IMAGE_RESOURCE_LINK_META_KEY = "mistralai.vibe.harness/file-image-resource-link"

_IMAGE_SUFFIX_BY_MIME_TYPE = {
    "image/gif": ".gif",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


def build_file_image_content_block(
    *, data: str, uri: str, name: str, mime_type: str, size: int
) -> RustImageContentBlock:
    resource_link = RustResourceLinkContentBlock(
        uri=uri, name=name, mime_type=mime_type, size=size
    )
    image = RustImageContentBlock(data=data, mime_type=mime_type)
    return image.model_copy(
        update={
            "meta": {
                FILE_IMAGE_RESOURCE_LINK_META_KEY: resource_link.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
            }
        }
    )


def export_file_image_fallback(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    """Move a valid Core fallback out of opaque metadata for interop transfer."""
    if value.get("type") != "image":
        return value
    meta = value.get("_meta")
    if not isinstance(meta, dict):
        return value
    raw_fallback = meta.get(FILE_IMAGE_RESOURCE_LINK_META_KEY)
    if raw_fallback is None:
        return value
    try:
        resource_link = RustResourceLinkContentBlock.model_validate(raw_fallback)
    except ValidationError:
        return value
    if urlparse(resource_link.uri).scheme != "file":
        return value

    exported = dict(value)
    remaining_meta = {
        key: item
        for key, item in meta.items()
        if key != FILE_IMAGE_RESOURCE_LINK_META_KEY
    }
    if remaining_meta:
        exported["_meta"] = remaining_meta
    else:
        exported.pop("_meta", None)
    exported["file_fallback"] = {"type": "file", "name": resource_link.name}
    return exported


def materialize_file_image_fallback(
    image: RustImageContentBlock, *, name: str, attachments_root: Path, keep_meta: bool
) -> RustImageContentBlock:
    """Write imported image bytes into the destination session and link them."""
    try:
        raw = base64.b64decode(image.data, validate=True)
    except ValueError as exc:
        raise ValueError("Imported file image is not valid base64") from exc

    suffix = _IMAGE_SUFFIX_BY_MIME_TYPE.get(image.mime_type, ".img")
    digest = hashlib.sha256(raw).hexdigest()
    attachments_root.mkdir(parents=True, exist_ok=True)
    destination = attachments_root / f"{digest}{suffix}"
    if not destination.exists():
        destination.write_bytes(raw)

    resource_link = RustResourceLinkContentBlock(
        uri=destination.resolve().as_uri(),
        name=name,
        mime_type=image.mime_type,
        size=len(raw),
    )
    meta = dict(image.meta or {}) if keep_meta else {}
    meta[FILE_IMAGE_RESOURCE_LINK_META_KEY] = resource_link.model_dump(
        mode="json", by_alias=True, exclude_none=True
    )
    return RustImageContentBlock(
        data=image.data,
        mime_type=image.mime_type,
        annotations=image.annotations,
        _meta=meta,
    )


__all__ = [
    "FILE_IMAGE_RESOURCE_LINK_META_KEY",
    "build_file_image_content_block",
    "export_file_image_fallback",
    "materialize_file_image_fallback",
]
