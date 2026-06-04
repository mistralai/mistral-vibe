from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.app import _ImageAttachmentRejection
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import ErrorMessage
from vibe.core.autocompletion.path_prompt import build_path_prompt_payload
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.types import (
    MAX_IMAGE_BYTES,
    MAX_IMAGES_PER_MESSAGE,
    Backend,
    ImageAttachment,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _vision_config(*, supports_images: bool = True):
    models = [
        ModelConfig(
            name="mistral-vibe-cli-latest",
            provider="mistral",
            alias="devstral-latest",
            supports_images=supports_images,
        )
    ]
    providers = [
        ProviderConfig(
            name="mistral",
            api_base="https://api.mistral.ai/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.MISTRAL,
        )
    ]
    return build_test_vibe_config(
        active_model="devstral-latest", models=models, providers=providers
    )


@pytest.mark.asyncio
async def test_build_image_attachments_happy_path(tmp_path: Path) -> None:
    (tmp_path / "shot.png").write_bytes(PNG_BYTES)
    payload = build_path_prompt_payload("look at @shot.png", base_dir=tmp_path)

    app = build_test_vibe_app(config=_vision_config())
    result = await app._build_image_attachments(payload)

    assert isinstance(result, list)
    assert len(result) == 1
    assert isinstance(result[0], ImageAttachment)
    assert result[0].alias == "shot.png"
    assert result[0].mime_type == "image/png"


@pytest.mark.asyncio
async def test_build_image_attachments_returns_empty_when_no_images(
    tmp_path: Path,
) -> None:
    (tmp_path / "notes.md").write_text("hi")
    payload = build_path_prompt_payload("read @notes.md", base_dir=tmp_path)

    app = build_test_vibe_app(config=_vision_config())
    result = await app._build_image_attachments(payload)

    assert result == []


@pytest.mark.asyncio
async def test_build_image_attachments_rejects_too_many_images(tmp_path: Path) -> None:
    mentions = []
    for i in range(MAX_IMAGES_PER_MESSAGE + 1):
        name = f"img{i}.png"
        (tmp_path / name).write_bytes(PNG_BYTES)
        mentions.append(f"@{name}")
    payload = build_path_prompt_payload(" ".join(mentions), base_dir=tmp_path)

    app = build_test_vibe_app(config=_vision_config())
    result = await app._build_image_attachments(payload)

    assert isinstance(result, _ImageAttachmentRejection)
    assert not result.no_vision
    assert "Too many image attachments" in result.message
    assert str(MAX_IMAGES_PER_MESSAGE) in result.message


@pytest.mark.asyncio
async def test_build_image_attachments_rejects_non_vision_model(tmp_path: Path) -> None:
    (tmp_path / "shot.png").write_bytes(PNG_BYTES)
    payload = build_path_prompt_payload("look at @shot.png", base_dir=tmp_path)

    app = build_test_vibe_app(config=_vision_config(supports_images=False))
    result = await app._build_image_attachments(payload)

    assert isinstance(result, _ImageAttachmentRejection)
    assert result.no_vision
    assert "does not support images" in result.message
    assert "devstral-latest" in result.message


@pytest.mark.asyncio
async def test_build_image_attachments_rejects_oversize_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Patch the cap down so we don't need to write 10MB to disk.
    monkeypatch.setattr("vibe.cli.textual_ui.app.MAX_IMAGE_BYTES", 32)

    (tmp_path / "shot.png").write_bytes(PNG_BYTES + b"\x00" * 64)
    payload = build_path_prompt_payload("look at @shot.png", base_dir=tmp_path)

    app = build_test_vibe_app(config=_vision_config())
    result = await app._build_image_attachments(payload)

    assert isinstance(result, _ImageAttachmentRejection)
    assert not result.no_vision
    assert "shot.png" in result.message
    assert "max" in result.message.lower()


def test_max_image_bytes_default_is_10_mib() -> None:
    assert MAX_IMAGE_BYTES == 10 * 1024 * 1024


def test_max_images_per_message_default_is_8() -> None:
    assert MAX_IMAGES_PER_MESSAGE == 8


@pytest.mark.asyncio
async def test_submit_restores_input_when_image_validation_fails(
    tmp_path: Path,
) -> None:
    (tmp_path / "shot.png").write_bytes(PNG_BYTES)
    app = build_test_vibe_app(config=_vision_config(supports_images=False))
    typed = f"look at @{tmp_path / 'shot.png'}"

    async with app.run_test() as pilot:
        chat_input = app.query_one(ChatInputContainer)
        chat_input.value = typed
        await pilot.press("enter")
        await pilot.pause()

        assert chat_input.value == typed
        errors = list(app.query(ErrorMessage))
        assert errors
        assert all(not e._show_border for e in errors)
