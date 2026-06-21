from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import pytest

from vibe.core.session.image_snapshot import (
    ImageSnapshotError,
    snapshot_image,
    snapshot_image_bytes,
)
from vibe.core.types import MAX_IMAGE_BYTES, FileImageSource, InlineImageSource

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_snapshot_image_copies_to_attachments_dir(tmp_path: Path) -> None:
    src = tmp_path / "screenshot.png"
    src.write_bytes(PNG_BYTES)
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    att = snapshot_image(src, alias="screenshot.png", session_dir=session_dir)

    digest = hashlib.sha1(PNG_BYTES, usedforsecurity=False).hexdigest()
    assert isinstance(att.source, FileImageSource)
    assert att.source.path == (session_dir / "attachments" / f"{digest}.png").resolve()
    assert att.alias == "screenshot.png"
    assert att.mime_type == "image/png"
    assert att.source.path.read_bytes() == PNG_BYTES


def test_snapshot_image_is_idempotent_on_same_bytes(tmp_path: Path) -> None:
    src_a = tmp_path / "a.png"
    src_b = tmp_path / "b.png"
    src_a.write_bytes(PNG_BYTES)
    src_b.write_bytes(PNG_BYTES)
    session_dir = tmp_path / "session"

    att_a = snapshot_image(src_a, alias="a.png", session_dir=session_dir)
    att_b = snapshot_image(src_b, alias="b.png", session_dir=session_dir)

    assert isinstance(att_a.source, FileImageSource)
    assert isinstance(att_b.source, FileImageSource)
    assert att_a.source.path == att_b.source.path
    assert sum(1 for _ in (session_dir / "attachments").iterdir()) == 1


def test_snapshot_image_returns_source_when_session_dir_is_none(tmp_path: Path) -> None:
    src = tmp_path / "screenshot.png"
    src.write_bytes(PNG_BYTES)

    att = snapshot_image(src, alias="screenshot.png", session_dir=None)

    assert isinstance(att.source, FileImageSource)
    assert att.source.path == src.resolve()
    assert att.alias == "screenshot.png"


def test_snapshot_image_rejects_non_image_extension(tmp_path: Path) -> None:
    src = tmp_path / "readme.txt"
    src.write_bytes(b"hi")

    with pytest.raises(ImageSnapshotError):
        snapshot_image(src, alias="readme.txt", session_dir=None)


def test_snapshot_image_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ImageSnapshotError):
        snapshot_image(tmp_path / "missing.png", alias="missing.png", session_dir=None)


def test_snapshot_image_normalizes_jpg_to_jpeg_mime(tmp_path: Path) -> None:
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 8)

    att = snapshot_image(src, alias="photo.jpg", session_dir=None)

    assert att.mime_type == "image/jpeg"


def test_snapshot_image_bytes_inlines_when_session_dir_is_none() -> None:
    att = snapshot_image_bytes(
        PNG_BYTES, alias="pasted.png", mime_type="image/png", session_dir=None
    )

    assert isinstance(att.source, InlineImageSource)
    assert att.source.data == base64.b64encode(PNG_BYTES).decode("ascii")
    assert att.alias == "pasted.png"
    assert att.mime_type == "image/png"


def test_snapshot_image_bytes_writes_file_when_session_dir_is_set(
    tmp_path: Path,
) -> None:
    session_dir = tmp_path / "session"

    att = snapshot_image_bytes(
        PNG_BYTES, alias="pasted.png", mime_type="image/png", session_dir=session_dir
    )

    digest = hashlib.sha1(PNG_BYTES, usedforsecurity=False).hexdigest()
    assert isinstance(att.source, FileImageSource)
    assert att.source.path == (session_dir / "attachments" / f"{digest}.png").resolve()
    assert att.source.path.read_bytes() == PNG_BYTES


def test_snapshot_image_bytes_rejects_unsupported_mime() -> None:
    with pytest.raises(ImageSnapshotError):
        snapshot_image_bytes(
            PNG_BYTES, alias="x", mime_type="image/tiff", session_dir=None
        )


def test_snapshot_image_rejects_oversized_file(tmp_path: Path) -> None:
    src = tmp_path / "big.png"
    src.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * MAX_IMAGE_BYTES)

    with pytest.raises(ImageSnapshotError):
        snapshot_image(src, alias="big.png", session_dir=None)


def test_snapshot_image_bytes_rejects_oversized_data() -> None:
    with pytest.raises(ImageSnapshotError):
        snapshot_image_bytes(
            b"x" * (MAX_IMAGE_BYTES + 1),
            alias="big.png",
            mime_type="image/png",
            session_dir=None,
        )
