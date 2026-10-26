from __future__ import annotations

from pathlib import Path

import pytest

from vibe.app_server._turn_input import session_content_blocks, vibe_content_blocks
from vibe.app_server.models import (
    FileImageSource,
    ImageAttachment,
    ImageContentBlock,
    InlineImageSource,
)
from vibe.app_server.protocol import SessionImageContentBlock, SessionTextContentBlock
from vibe.utils import paths


def test_vibe_content_blocks_decode_windows_file_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(paths, "is_windows", lambda: True)
    block = SessionImageContentBlock(
        uri="file:///C:/Users/acmedev/image%20one.png",
        media_type="image/png",
        alt_text="image one",
    )

    content = vibe_content_blocks([block])

    image = content[0]
    assert isinstance(image, ImageContentBlock)
    assert isinstance(image.attachment.source, FileImageSource)
    assert image.attachment.source.path == r"C:\Users\acmedev\image one.png"


def test_a_file_image_reaches_the_session_with_its_file_link(tmp_path: Path) -> None:
    """*Prepare*: A user message with an image from a file and a pasted inline image.
    *Do*: Build the session turn input.
    *Assert*: The file image is a standard image block named by its file URI, so
    the Harness keeps the link; the inline image stays inline and no file is saved.
    """
    # Prepare
    image_path = tmp_path / "shot.png"
    image_path.write_bytes(b"image")
    file_image = ImageAttachment(
        source=FileImageSource(path=str(image_path)),
        alias="shot.png",
        mime_type="image/png",
    )
    pasted = ImageAttachment(
        source=InlineImageSource(data="aW1hZ2U="), alias="image", mime_type="image/png"
    )

    # Do
    content = session_content_blocks("look", [file_image, pasted])

    # Assert
    assert content == [
        SessionTextContentBlock(text="look"),
        SessionImageContentBlock(
            uri=image_path.resolve().as_uri(),
            media_type="image/png",
            alt_text="shot.png",
        ),
        SessionImageContentBlock(
            uri="data:image/png;base64,aW1hZ2U=",
            media_type="image/png",
            alt_text="image",
        ),
    ]
    assert list(tmp_path.iterdir()) == [image_path]
