from __future__ import annotations

from pathlib import Path

import pytest

from tests.mock.utils import collect_result
from vibe.core.scratchpad import init_scratchpad
from vibe.core.tools.base import InvokeContext, ToolError
from vibe.core.tools.builtins.read_image import (
    ReadImage,
    ReadImageArgs,
    ReadImageConfig,
    ReadImageResult,
    ReadImageState,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay
from vibe.core.types import (
    FileImageSource,
    ImageAttachment,
    ToolResultEvent,
    ToolStreamEvent,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _make_read_image() -> ReadImage:
    return ReadImage(config_getter=lambda: ReadImageConfig(), state=ReadImageState())


# -- run() success cases ---------------------------------------------------


@pytest.mark.asyncio
async def test_reads_valid_png(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(PNG_BYTES)
    tool = _make_read_image()

    result = await collect_result(tool.run(ReadImageArgs(path=str(img))))

    assert isinstance(result, ReadImageResult)
    assert result.path == str(img.resolve())
    assert result.alias == "shot.png"
    assert result.mime_type == "image/png"
    assert result.image_path == str(img.resolve())


@pytest.mark.asyncio
async def test_reads_jpg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    img = tmp_path / "photo.jpg"
    img.write_bytes(PNG_BYTES)
    tool = _make_read_image()

    result = await collect_result(tool.run(ReadImageArgs(path=str(img))))

    assert result.alias == "photo.jpg"
    assert result.mime_type == "image/jpeg"


@pytest.mark.asyncio
async def test_reads_webp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    img = tmp_path / "img.webp"
    img.write_bytes(PNG_BYTES)
    tool = _make_read_image()

    result = await collect_result(tool.run(ReadImageArgs(path=str(img))))

    assert result.alias == "img.webp"
    assert result.mime_type == "image/webp"


@pytest.mark.asyncio
async def test_reads_gif(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    img = tmp_path / "anim.gif"
    img.write_bytes(PNG_BYTES)
    tool = _make_read_image()

    result = await collect_result(tool.run(ReadImageArgs(path=str(img))))

    assert result.alias == "anim.gif"
    assert result.mime_type == "image/gif"


@pytest.mark.asyncio
async def test_reads_jpeg_extension(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    img = tmp_path / "photo.jpeg"
    img.write_bytes(PNG_BYTES)
    tool = _make_read_image()

    result = await collect_result(tool.run(ReadImageArgs(path=str(img))))

    assert result.alias == "photo.jpeg"
    assert result.mime_type == "image/jpeg"


@pytest.mark.asyncio
async def test_relative_path_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "sub").mkdir()
    img = tmp_path / "sub" / "img.png"
    img.write_bytes(PNG_BYTES)
    tool = _make_read_image()

    result = await collect_result(tool.run(ReadImageArgs(path="sub/img.png")))

    assert result.path == str(img.resolve())
    assert result.alias == "img.png"


@pytest.mark.asyncio
async def test_yields_stream_event_with_ctx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(PNG_BYTES)
    tool = _make_read_image()
    ctx = InvokeContext(tool_call_id="call-1")

    events: list[ToolStreamEvent | ReadImageResult] = []
    async for item in tool.run(ReadImageArgs(path=str(img)), ctx):
        events.append(item)

    assert len(events) == 2
    assert isinstance(events[0], ToolStreamEvent)
    assert events[0].message == "Reading image..."
    assert events[0].tool_call_id == "call-1"
    assert isinstance(events[1], ReadImageResult)
    assert events[1].alias == "shot.png"


@pytest.mark.asyncio
async def test_no_stream_event_without_ctx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    img = tmp_path / "shot.png"
    img.write_bytes(PNG_BYTES)
    tool = _make_read_image()

    events: list[ToolStreamEvent | ReadImageResult] = []
    async for item in tool.run(ReadImageArgs(path=str(img))):
        events.append(item)

    assert len(events) == 1
    assert isinstance(events[0], ReadImageResult)


# -- run() error cases -----------------------------------------------------


@pytest.mark.asyncio
async def test_unsupported_extension_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "doc.txt").write_text("not an image", encoding="utf-8")
    tool = _make_read_image()

    with pytest.raises(ToolError, match="Unsupported image extension"):
        await collect_result(tool.run(ReadImageArgs(path=str(tmp_path / "doc.txt"))))


@pytest.mark.asyncio
async def test_file_not_found_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    tool = _make_read_image()

    with pytest.raises(ToolError, match="File not found"):
        await collect_result(tool.run(ReadImageArgs(path=str(tmp_path / "nope.png"))))


@pytest.mark.asyncio
async def test_directory_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pics").mkdir()
    tool = _make_read_image()

    with pytest.raises(ToolError, match="directory"):
        await collect_result(tool.run(ReadImageArgs(path=str(tmp_path / "pics"))))


@pytest.mark.asyncio
async def test_empty_path_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    tool = _make_read_image()

    with pytest.raises(ToolError, match="path cannot be empty"):
        await collect_result(tool.run(ReadImageArgs(path="")))


# -- get_result_images -----------------------------------------------------


def test_get_result_images_returns_attachment() -> None:
    tool = _make_read_image()
    result = ReadImageResult(
        path="/img/shot.png",
        alias="shot.png",
        mime_type="image/png",
        image_path="/tmp/snaps/abc123.png",
    )
    images = tool.get_result_images(result)
    assert images is not None
    assert len(images) == 1
    att = images[0]
    assert isinstance(att, ImageAttachment)
    assert isinstance(att.source, FileImageSource)
    assert str(att.source.path) == "/tmp/snaps/abc123.png"
    assert att.alias == "shot.png"
    assert att.mime_type == "image/png"


def test_get_result_images_returns_none_when_no_image_path() -> None:
    tool = _make_read_image()
    result = ReadImageResult(
        path="/img/shot.png", alias="shot.png", mime_type="image/png", image_path=""
    )
    assert tool.get_result_images(result) is None


# -- image_path excluded from model_dump -----------------------------------


def test_image_path_excluded_from_dump() -> None:
    result = ReadImageResult(
        path="/img/shot.png",
        alias="shot.png",
        mime_type="image/png",
        image_path="secret/path.png",
    )
    dumped = result.model_dump()
    assert "image_path" not in dumped
    assert dumped["path"] == "/img/shot.png"
    assert dumped["alias"] == "shot.png"
    assert dumped["mime_type"] == "image/png"


# -- display methods -------------------------------------------------------


def test_format_call_display() -> None:
    args = ReadImageArgs(path="/team/logo.png")
    display = ReadImage.format_call_display(args)

    assert isinstance(display, ToolCallDisplay)
    assert "/team/logo.png" in display.summary
    assert display.suffix == ""


def test_format_call_display_scratchpad_suffix() -> None:
    sp = init_scratchpad("read-image-display")
    assert sp is not None
    img_path = sp / "shot.png"

    display = ReadImage.format_call_display(ReadImageArgs(path=str(img_path)))

    assert isinstance(display, ToolCallDisplay)
    assert display.suffix == "(scratchpad)"


def test_get_result_display_success() -> None:
    result = ReadImageResult(
        path="/img/shot.png",
        alias="shot.png",
        mime_type="image/png",
        image_path="/snaps/abc.png",
    )
    event = ToolResultEvent(
        tool_call_id="test", tool_name="read_image", tool_class=None, result=result
    )
    display = ReadImage.get_result_display(event)

    assert isinstance(display, ToolResultDisplay)
    assert display.success is True
    assert "shot.png" in display.message
    assert display.suffix == ""


def test_get_result_display_scratchpad_suffix() -> None:
    sp = init_scratchpad("read-image-result-display")
    assert sp is not None
    img_path = sp / "shot.png"
    result = ReadImageResult(
        path=str(img_path),
        alias="shot.png",
        mime_type="image/png",
        image_path=str(img_path),
    )
    event = ToolResultEvent(
        tool_call_id="test", tool_name="read_image", tool_class=None, result=result
    )

    display = ReadImage.get_result_display(event)

    assert isinstance(display, ToolResultDisplay)
    assert display.success is True
    assert display.suffix == "(scratchpad)"


def test_get_result_display_with_error() -> None:
    event = ToolResultEvent(
        tool_call_id="test",
        tool_name="read_image",
        tool_class=None,
        result=None,
        error="Something went wrong",
    )
    display = ReadImage.get_result_display(event)

    assert isinstance(display, ToolResultDisplay)
    assert display.success is False
    assert "Something went wrong" in display.message


def test_get_result_display_fallback() -> None:
    event = ToolResultEvent(
        tool_call_id="test",
        tool_name="read_image",
        tool_class=None,
        result=None,
        skip_reason="Skipped by user",
    )
    display = ReadImage.get_result_display(event)

    assert display.success is False
    assert "Skipped by user" in display.message


def test_get_status_text() -> None:
    assert ReadImage.get_status_text() == "Reading image"
