from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
import pytest

from vibe.core.types import FileImageSource, ImageAttachment, InlineImageSource


def test_migrates_legacy_flat_path_shape() -> None:
    att = ImageAttachment.model_validate({
        "path": "/tmp/a.png",
        "alias": "a.png",
        "mime_type": "image/png",
    })

    assert isinstance(att.source, FileImageSource)
    assert att.source.path == Path("/tmp/a.png")


def test_migrates_legacy_flat_data_shape() -> None:
    att = ImageAttachment.model_validate({
        "data": "Zm9v",
        "alias": "pasted.png",
        "mime_type": "image/png",
    })

    assert isinstance(att.source, InlineImageSource)
    assert att.source.data == "Zm9v"


def test_source_path_construction() -> None:
    att = ImageAttachment(
        source=FileImageSource(path=Path("/tmp/a.png")),
        alias="a.png",
        mime_type="image/png",
    )

    assert isinstance(att.source, FileImageSource)
    assert att.source.path == Path("/tmp/a.png")


def test_file_source_round_trips_through_json() -> None:
    att = ImageAttachment(
        source=FileImageSource(path=Path("/tmp/a.png")),
        alias="a.png",
        mime_type="image/png",
    )

    dumped = att.model_dump(exclude_none=True, mode="json")
    assert dumped["source"] == {"kind": "file", "path": "/tmp/a.png"}
    assert ImageAttachment.model_validate(dumped) == att


def test_rejects_attachment_without_source() -> None:
    with pytest.raises(ValidationError):
        ImageAttachment.model_validate({"alias": "a.png", "mime_type": "image/png"})
