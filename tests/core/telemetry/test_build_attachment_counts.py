from __future__ import annotations

from pathlib import Path

from vibe.core.telemetry.build_metadata import build_attachment_counts
from vibe.core.telemetry.types import AttachmentKind
from vibe.core.types import FileImageSource, ImageAttachment, LLMMessage, Role


def _msg(images: list[ImageAttachment] | None) -> LLMMessage:
    return LLMMessage(role=Role.user, content="hi", images=images)


def _image() -> ImageAttachment:
    return ImageAttachment(
        source=FileImageSource(path=Path("/tmp/x.png")),
        alias="x.png",
        mime_type="image/png",
    )


def test_returns_empty_when_message_is_none() -> None:
    assert build_attachment_counts(None, supports_images=True) == {}


def test_returns_empty_when_no_images() -> None:
    assert build_attachment_counts(_msg(None), supports_images=True) == {}


def test_counts_images_when_model_supports_them() -> None:
    counts = build_attachment_counts(_msg([_image(), _image()]), supports_images=True)
    assert counts == {AttachmentKind.IMAGE: 2}


def test_drops_images_when_model_does_not_support_them() -> None:
    assert build_attachment_counts(_msg([_image()]), supports_images=False) == {}
