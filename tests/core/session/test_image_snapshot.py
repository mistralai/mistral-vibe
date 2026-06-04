from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from vibe.core.session.image_snapshot import ImageSnapshotError, snapshot_image

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def test_snapshot_image_copies_to_attachments_dir(tmp_path: Path) -> None:
    src = tmp_path / "screenshot.png"
    src.write_bytes(PNG_BYTES)
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    att = snapshot_image(src, alias="screenshot.png", session_dir=session_dir)

    digest = hashlib.sha1(PNG_BYTES, usedforsecurity=False).hexdigest()
    assert att.path == (session_dir / "attachments" / f"{digest}.png").resolve()
    assert att.alias == "screenshot.png"
    assert att.mime_type == "image/png"
    assert att.path.read_bytes() == PNG_BYTES


def test_snapshot_image_is_idempotent_on_same_bytes(tmp_path: Path) -> None:
    src_a = tmp_path / "a.png"
    src_b = tmp_path / "b.png"
    src_a.write_bytes(PNG_BYTES)
    src_b.write_bytes(PNG_BYTES)
    session_dir = tmp_path / "session"

    att_a = snapshot_image(src_a, alias="a.png", session_dir=session_dir)
    att_b = snapshot_image(src_b, alias="b.png", session_dir=session_dir)

    assert att_a.path == att_b.path
    assert sum(1 for _ in (session_dir / "attachments").iterdir()) == 1


def test_snapshot_image_returns_source_when_session_dir_is_none(tmp_path: Path) -> None:
    src = tmp_path / "screenshot.png"
    src.write_bytes(PNG_BYTES)

    att = snapshot_image(src, alias="screenshot.png", session_dir=None)

    assert att.path == src.resolve()
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
