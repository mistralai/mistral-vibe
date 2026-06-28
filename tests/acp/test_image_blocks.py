from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from acp.helpers import ImageContentBlock, TextContentBlock
import pytest

from vibe.acp.exceptions import INVALID_IMAGE_ATTACHMENT, InvalidImageAttachmentError
from vibe.acp.image_blocks import extract_image_attachments
from vibe.core.types import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_MESSAGE,
    FileImageSource,
    InlineImageSource,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _image_block(uri: str | None = None) -> ImageContentBlock:
    return ImageContentBlock(
        type="image",
        data=base64.b64encode(PNG_BYTES).decode("ascii"),
        mime_type="image/png",
        uri=uri,
    )


def test_inlines_bytes_when_session_dir_is_none() -> None:
    [att] = extract_image_attachments([_image_block()], session_dir=None)

    assert isinstance(att.source, InlineImageSource)
    assert att.source.data == base64.b64encode(PNG_BYTES).decode("ascii")
    assert att.mime_type == "image/png"


def test_writes_attachment_file_when_session_dir_is_set(tmp_path: Path) -> None:
    [att] = extract_image_attachments([_image_block()], session_dir=tmp_path)

    digest = hashlib.sha1(PNG_BYTES, usedforsecurity=False).hexdigest()
    assert isinstance(att.source, FileImageSource)
    assert att.source.path == (tmp_path / "attachments" / f"{digest}.png").resolve()
    assert att.source.path.read_bytes() == PNG_BYTES


def test_derives_alias_from_uri_basename() -> None:
    [att] = extract_image_attachments([_image_block(uri="cat.png")], session_dir=None)

    assert att.alias == "cat.png"


def test_falls_back_to_default_alias_without_uri() -> None:
    [att] = extract_image_attachments([_image_block()], session_dir=None)

    assert att.alias == "pasted-image.png"


def test_ignores_non_image_blocks() -> None:
    block = TextContentBlock(type="text", text="hello")

    assert extract_image_attachments([block], session_dir=None) == []


def test_rejects_unsupported_mime() -> None:
    block = ImageContentBlock(
        type="image",
        data=base64.b64encode(PNG_BYTES).decode("ascii"),
        mime_type="image/tiff",
    )

    with pytest.raises(InvalidImageAttachmentError):
        extract_image_attachments([block], session_dir=None)


def test_image_block_error_is_structured_acp_error() -> None:
    block = ImageContentBlock(
        type="image",
        data=base64.b64encode(PNG_BYTES).decode("ascii"),
        mime_type="image/tiff",
    )

    with pytest.raises(InvalidImageAttachmentError) as exc_info:
        extract_image_attachments([block], session_dir=None)

    assert exc_info.value.code == INVALID_IMAGE_ATTACHMENT
    assert exc_info.value.data == {"reason": "wrong_type"}


def test_rejects_invalid_base64() -> None:
    block = ImageContentBlock(type="image", data="not base64!!!", mime_type="image/png")

    with pytest.raises(InvalidImageAttachmentError):
        extract_image_attachments([block], session_dir=None)


def test_rejects_too_many_images() -> None:
    blocks = [_image_block() for _ in range(MAX_IMAGES_PER_MESSAGE + 1)]

    with pytest.raises(InvalidImageAttachmentError):
        extract_image_attachments(blocks, session_dir=None)


def test_rejects_oversized_image() -> None:
    block = ImageContentBlock(
        type="image",
        data=base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode("ascii"),
        mime_type="image/png",
    )

    with pytest.raises(InvalidImageAttachmentError):
        extract_image_attachments([block], session_dir=None)
