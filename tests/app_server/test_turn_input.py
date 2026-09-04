from __future__ import annotations

import pytest

from vibe.app_server._turn_input import vibe_content_blocks
from vibe.app_server.models import FileImageSource, ImageContentBlock
from vibe.app_server.protocol import SessionImageContentBlock
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
