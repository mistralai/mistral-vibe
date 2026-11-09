from __future__ import annotations

import base64

from acp.helpers import ImageContentBlock, TextContentBlock
import pytest

from vibe.acp.exceptions import INVALID_IMAGE_ATTACHMENT, InvalidImageAttachmentError
from vibe.acp.image_blocks import extract_image_attachments
from vibe.app_server.models import InlineImageSource
from vibe.utils.images import MAX_IMAGE_BYTES, MAX_IMAGES_PER_MESSAGE

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _image_block(uri: str | None = None) -> ImageContentBlock:
    return ImageContentBlock(
        type="image",
        data=base64.b64encode(PNG_BYTES).decode("ascii"),
        mime_type="image/png",
        uri=uri,
    )


def test_decodes_acp_images_at_the_app_server_boundary() -> None:
    [att] = extract_image_attachments([_image_block()])

    assert isinstance(att.source, InlineImageSource)
    assert att.source.data == base64.b64encode(PNG_BYTES).decode("ascii")
    assert att.mime_type == "image/png"


def test_derives_alias_from_uri_basename() -> None:
    [att] = extract_image_attachments([_image_block(uri="cat.png")])

    assert att.alias == "cat.png"


def test_falls_back_to_default_alias_without_uri() -> None:
    [att] = extract_image_attachments([_image_block()])

    assert att.alias == "pasted-image.png"


def test_ignores_non_image_blocks() -> None:
    block = TextContentBlock(type="text", text="hello")

    assert extract_image_attachments([block]) == []


def test_rejects_unsupported_mime() -> None:
    block = ImageContentBlock(
        type="image",
        data=base64.b64encode(PNG_BYTES).decode("ascii"),
        mime_type="image/tiff",
    )

    with pytest.raises(InvalidImageAttachmentError):
        extract_image_attachments([block])


def test_image_block_error_is_structured_acp_error() -> None:
    block = ImageContentBlock(
        type="image",
        data=base64.b64encode(PNG_BYTES).decode("ascii"),
        mime_type="image/tiff",
    )

    with pytest.raises(InvalidImageAttachmentError) as exc_info:
        extract_image_attachments([block])

    assert exc_info.value.code == INVALID_IMAGE_ATTACHMENT
    assert exc_info.value.data == {"reason": "wrong_type"}


def test_rejects_invalid_base64() -> None:
    block = ImageContentBlock(type="image", data="not base64!!!", mime_type="image/png")

    with pytest.raises(InvalidImageAttachmentError):
        extract_image_attachments([block])


def test_rejects_too_many_images() -> None:
    blocks = [_image_block() for _ in range(MAX_IMAGES_PER_MESSAGE + 1)]

    with pytest.raises(InvalidImageAttachmentError):
        extract_image_attachments(blocks)


def test_rejects_oversized_image() -> None:
    block = ImageContentBlock(
        type="image",
        data=base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode("ascii"),
        mime_type="image/png",
    )

    with pytest.raises(InvalidImageAttachmentError):
        extract_image_attachments([block])
