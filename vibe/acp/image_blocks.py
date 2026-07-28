from __future__ import annotations

import base64
import binascii
from collections.abc import Sequence
from pathlib import Path

from acp.helpers import ContentBlock, ImageContentBlock

from vibe.acp.exceptions import InvalidImageAttachmentError
from vibe.app_server.models import ImageAttachment, InlineImageSource
from vibe.utils.images import MAX_IMAGE_BYTES, MAX_IMAGES_PER_MESSAGE

_IMAGE_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


def extract_image_attachments(blocks: Sequence[ContentBlock]) -> list[ImageAttachment]:
    image_blocks = [block for block in blocks if isinstance(block, ImageContentBlock)]
    if len(image_blocks) > MAX_IMAGES_PER_MESSAGE:
        raise InvalidImageAttachmentError(
            f"Too many images: {len(image_blocks)} > {MAX_IMAGES_PER_MESSAGE}",
            reason="too_many",
        )

    return [_block_to_attachment(block) for block in image_blocks]


def _block_to_attachment(block: ImageContentBlock) -> ImageAttachment:
    ext = _IMAGE_EXTENSIONS.get(block.mime_type)
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

    return ImageAttachment(
        source=InlineImageSource(data=block.data),
        alias=alias,
        mime_type=block.mime_type,
    )
