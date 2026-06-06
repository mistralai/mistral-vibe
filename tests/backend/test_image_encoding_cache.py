from __future__ import annotations

import base64
from pathlib import Path

import pytest

from vibe.core.llm.backend._image import (
    ImageReadError,
    _encode_cached,
    to_base64,
    to_data_uri,
)
from vibe.core.types import ImageAttachment

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    _encode_cached.cache_clear()


def _att(tmp_path: Path, name: str = "shot.png") -> ImageAttachment:
    p = tmp_path / name
    p.write_bytes(PNG_BYTES)
    return ImageAttachment(path=p, alias=name, mime_type="image/png")


def test_repeated_calls_hit_cache_and_skip_disk(tmp_path: Path) -> None:
    att = _att(tmp_path)

    first = to_base64(att)
    second = to_base64(att)
    third = to_data_uri(att)

    assert first == second == base64.b64encode(PNG_BYTES).decode("ascii")
    assert third == f"data:image/png;base64,{first}"
    info = _encode_cached.cache_info()
    assert info.misses == 1
    assert info.hits == 2


def test_cache_invalidates_when_file_mtime_changes(tmp_path: Path) -> None:
    att = _att(tmp_path)
    to_base64(att)
    assert _encode_cached.cache_info().misses == 1

    new_bytes = PNG_BYTES + b"\x01"
    att.path.write_bytes(new_bytes)
    # Force a distinct mtime even on coarse-resolution filesystems.
    import os

    stat = att.path.stat()
    os.utime(att.path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    refreshed = to_base64(att)
    assert refreshed == base64.b64encode(new_bytes).decode("ascii")
    assert _encode_cached.cache_info().misses == 2


def test_missing_file_raises_image_read_error(tmp_path: Path) -> None:
    att = ImageAttachment(
        path=tmp_path / "nope.png", alias="nope.png", mime_type="image/png"
    )

    with pytest.raises(ImageReadError):
        to_data_uri(att)
