from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from pathlib import Path

from acp.helpers import ContentBlock, ImageContentBlock

from vibe.acp.exceptions import InvalidImageAttachmentError
from vibe.core.session.image_snapshot import (
    ImageSnapshotError,
    extension_for_mime,
    snapshot_image_bytes,
)
from vibe.core.types import MAX_IMAGE_BYTES, MAX_IMAGES_PER_MESSAGE, ImageAttachment


def extract_image_attachments(
    blocks: Sequence[ContentBlock], *, session_dir: Path | None
) -> list[ImageAttachment]:
    image_blocks = [block for block in blocks if isinstance(block, ImageContentBlock)]
    if len(image_blocks) > MAX_IMAGES_PER_MESSAGE:
        raise InvalidImageAttachmentError(
            f"Too many images: {len(image_blocks)} > {MAX_IMAGES_PER_MESSAGE}",
            reason="too_many",
        )

    return [
        _block_to_attachment(block, session_dir=session_dir) for block in image_blocks
    ]


def _block_to_attachment(
    block: ImageContentBlock, *, session_dir: Path | None
) -> ImageAttachment:
    ext = extension_for_mime(block.mime_type)
    if ext is None:
        raise InvalidImageAttachmentError(
            f"Unsupported image mime type: {block.mime_type}", reason="wrong_type"
        )

    try:
        data = base64.b64decode(block.data, validate=True)
    except (binascii.Error, ValueError) as e:
        raise InvalidImageAttachmentError(
            f"Invalid base64 image data: {e}", reason="invalid_base64"
        ) from e

    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidImageAttachmentError(
            f"Image is too large: {len(data)} > {MAX_IMAGE_BYTES}", reason="too_large"
        )

    alias = Path(block.uri).name if block.uri else f"pasted-image{ext}"

    try:
        return snapshot_image_bytes(
            data, alias=alias, mime_type=block.mime_type, session_dir=session_dir
        )
    except ImageSnapshotError as e:
        raise InvalidImageAttachmentError(str(e), reason="snapshot_failed") from e
