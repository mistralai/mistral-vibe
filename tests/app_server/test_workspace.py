from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.app_server._workspace import PromptPreparationError, prepare_prompt
from vibe.app_server.models import ImageAttachment
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.types import Backend
from vibe.utils.images import MAX_IMAGE_BYTES, MAX_IMAGES_PER_MESSAGE

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


def test_prepare_prompt_snapshots_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shot.png").write_bytes(PNG_BYTES)
    agent_loop = build_test_agent_loop(config=_vision_config())

    prompt = prepare_prompt(agent_loop, "look at @shot.png")

    assert len(prompt.images) == 1
    assert isinstance(prompt.images[0], ImageAttachment)
    assert prompt.images[0].alias == "shot.png"
    assert prompt.images[0].mime_type == "image/png"
    assert prompt.mentions.count == 1
    assert prompt.mentions.context_types == {"image": 1}


def test_prepare_prompt_never_embeds_mentioned_file_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    marker = "private contents must cross the read_file permission boundary"
    (tmp_path / "secret.txt").write_text(marker, encoding="utf-8")
    agent_loop = build_test_agent_loop()
    message = "read @secret.txt"

    prompt = prepare_prompt(agent_loop, message)

    assert prompt.display_text == message
    assert prompt.prompt_text == message
    assert marker not in prompt.model_dump_json()


def test_prepare_prompt_rejects_too_many_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    mentions = []
    for index in range(MAX_IMAGES_PER_MESSAGE + 1):
        name = f"img{index}.png"
        (tmp_path / name).write_bytes(PNG_BYTES)
        mentions.append(f"@{name}")
    agent_loop = build_test_agent_loop(config=_vision_config())

    with pytest.raises(PromptPreparationError, match="Too many image attachments"):
        prepare_prompt(agent_loop, " ".join(mentions))


def test_prepare_prompt_rejects_images_for_text_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shot.png").write_bytes(PNG_BYTES)
    agent_loop = build_test_agent_loop(config=_vision_config(supports_images=False))

    with pytest.raises(PromptPreparationError, match="does not support images"):
        prepare_prompt(agent_loop, "look at @shot.png")


def test_prepare_prompt_rejects_oversize_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("vibe.core.session.image_snapshot.MAX_IMAGE_BYTES", 32)
    (tmp_path / "shot.png").write_bytes(PNG_BYTES + b"\x00" * 64)
    agent_loop = build_test_agent_loop(config=_vision_config())

    with pytest.raises(PromptPreparationError, match="Image is too large"):
        prepare_prompt(agent_loop, "look at @shot.png")


def test_image_limits_match_public_cli_contract() -> None:
    assert MAX_IMAGE_BYTES == 10 * 1024 * 1024
    assert MAX_IMAGES_PER_MESSAGE == 8
