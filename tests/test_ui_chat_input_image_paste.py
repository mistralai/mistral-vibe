from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.chat_input.text_area import (
    ChatTextArea,
    _image_from_dropped_path,
)
from vibe.core.types import ImageContentPart

PNG_BYTES = b"\x89PNG\r\n\x1a\ntest-payload"


def _make_part(idx: int) -> ImageContentPart:
    return ImageContentPart(
        image_url=f"data:image/png;base64,AAA{idx}", media_type="image/png"
    )


@pytest.mark.asyncio
async def test_attach_image_placeholder_inserts_numbered_marker_and_stores_part(
    vibe_app: VibeApp,
) -> None:
    async with vibe_app.run_test():
        text_area = vibe_app.query_one(ChatTextArea)

        text_area._attach_image_placeholder(_make_part(1), size_kb=12)

        assert text_area.has_pending_images
        assert text_area.text == "[Image #1]"

        text_area._attach_image_placeholder(_make_part(2), size_kb=18)

        assert text_area.text == "[Image #1][Image #2]"
        assert len(text_area._pending_images) == 2


@pytest.mark.asyncio
async def test_take_pending_images_returns_list_and_clears_state(
    vibe_app: VibeApp,
) -> None:
    async with vibe_app.run_test():
        text_area = vibe_app.query_one(ChatTextArea)
        text_area._attach_image_placeholder(_make_part(1), size_kb=10)
        text_area._attach_image_placeholder(_make_part(2), size_kb=10)

        taken = text_area.take_pending_images()

        assert len(taken) == 2
        assert not text_area.has_pending_images
        assert text_area.take_pending_images() == []


@pytest.mark.asyncio
async def test_clear_text_also_clears_pending_images(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        text_area = vibe_app.query_one(ChatTextArea)
        text_area._attach_image_placeholder(_make_part(1), size_kb=10)
        assert text_area.has_pending_images

        text_area.clear_text()

        assert not text_area.has_pending_images
        assert text_area.text == ""


@pytest.mark.asyncio
async def test_chat_input_body_submitted_carries_pending_images(
    vibe_app: VibeApp,
) -> None:
    async with vibe_app.run_test():
        text_area = vibe_app.query_one(ChatTextArea)
        body = vibe_app.query_one(ChatInputBody)

        text_area._attach_image_placeholder(_make_part(1), size_kb=10)
        text_area.insert(" describe this")

        with patch.object(body, "post_message") as post_message:
            body.on_chat_text_area_submitted(
                ChatTextArea.Submitted("[Image #1] describe this")
            )

        post_message.assert_called_once()
        event = post_message.call_args.args[0]
        assert isinstance(event, ChatInputBody.Submitted)
        assert event.value == "[Image #1] describe this"
        assert event.image_parts is not None
        assert len(event.image_parts) == 1
        assert event.image_parts[0].image_url.startswith("data:image/png;base64,")
        # Pending images on the text area should have been consumed.
        assert not text_area.has_pending_images


@pytest.mark.asyncio
async def test_submitted_without_images_passes_none(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        body = vibe_app.query_one(ChatInputBody)

        with patch.object(body, "post_message") as post_message:
            body.on_chat_text_area_submitted(ChatTextArea.Submitted("plain text"))

        post_message.assert_called_once()
        event = post_message.call_args.args[0]
        assert isinstance(event, ChatInputBody.Submitted)
        assert event.image_parts is None


def test_image_from_dropped_path_reads_image_file(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(PNG_BYTES)
    result = _image_from_dropped_path(str(img))
    assert result is not None
    assert result == (PNG_BYTES, "image/png")


def test_image_from_dropped_path_strips_whitespace(tmp_path: Path) -> None:
    img = tmp_path / "shot.jpg"
    img.write_bytes(PNG_BYTES)
    result = _image_from_dropped_path(f"  {img}\n")
    assert result is not None
    assert result[1] == "image/jpeg"


def test_image_from_dropped_path_strips_quotes(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(PNG_BYTES)
    result = _image_from_dropped_path(f'"{img}"')
    assert result is not None


def test_image_from_dropped_path_rejects_relative_path() -> None:
    assert _image_from_dropped_path("./shot.png") is None


def test_image_from_dropped_path_rejects_multi_word_text(tmp_path: Path) -> None:
    img = tmp_path / "shot.png"
    img.write_bytes(PNG_BYTES)
    assert _image_from_dropped_path(f"hey {img}") is None


def test_image_from_dropped_path_rejects_non_image_extension(tmp_path: Path) -> None:
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    assert _image_from_dropped_path(str(txt)) is None


def test_image_from_dropped_path_rejects_missing_file(tmp_path: Path) -> None:
    ghost = tmp_path / "ghost.png"
    assert _image_from_dropped_path(str(ghost)) is None


def test_image_from_dropped_path_rejects_oversize_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    img = tmp_path / "big.png"
    img.write_bytes(PNG_BYTES)
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.chat_input.text_area.MAX_IMAGE_BYTES", 4
    )
    assert _image_from_dropped_path(str(img)) is None


@pytest.mark.asyncio
async def test_container_propagates_image_parts(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        container = vibe_app.query_one(ChatInputContainer)

        parts = [_make_part(1), _make_part(2)]
        with patch.object(container, "post_message") as post_message:
            container.on_chat_input_body_submitted(
                ChatInputBody.Submitted("hi", image_parts=parts)
            )

        post_message.assert_called_once()
        event = post_message.call_args.args[0]
        assert isinstance(event, ChatInputContainer.Submitted)
        assert event.value == "hi"
        assert event.image_parts == parts
