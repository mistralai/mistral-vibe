from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from vibe.app_server._workspace import (
    PromptPreparationError,
    mentioned_file_content_blocks,
    prepare_prompt,
    read_untrusted_config_dirs,
)
from vibe.app_server.models import ImageAttachment, ResourceContentBlock
from vibe.core.config import ModelConfig, ProviderConfig
from vibe.core.paths import TRUSTED_FOLDERS_FILE
from vibe.core.trusted_folders import TrustedFoldersManager
from vibe.core.types import Backend
from vibe.user_content import UserTextResource
from vibe.utils.images import MAX_IMAGE_BYTES, MAX_IMAGES_PER_MESSAGE

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _vision_config(*, supports_images: bool = True, display_name: str | None = None):
    models = [
        ModelConfig(
            name="mistral-vibe-cli-latest",
            provider="mistral",
            alias="devstral-latest",
            display_name=display_name,
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


def test_mentioned_file_content_blocks_attach_text_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "notes.md").write_text("hello world", encoding="utf-8")

    blocks = mentioned_file_content_blocks("read @notes.md", base_dir=tmp_path)

    assert len(blocks) == 1
    block = blocks[0]
    assert isinstance(block, ResourceContentBlock)
    assert isinstance(block.resource, UserTextResource)
    assert block.resource.uri == (tmp_path / "notes.md").as_uri()
    assert block.resource.text == "hello world"


def test_mentioned_file_content_blocks_cap_text_resources(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_text("x" * (60 * 1024), encoding="utf-8")

    [block] = mentioned_file_content_blocks("read @large.txt", base_dir=tmp_path)

    assert isinstance(block, ResourceContentBlock)
    assert isinstance(block.resource, UserTextResource)
    assert len(block.resource.text.encode("utf-8")) < 53 * 1024
    assert "truncated" in block.resource.text


def test_mentioned_file_content_blocks_reject_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("nope", encoding="utf-8")

    with pytest.raises(PromptPreparationError, match="outside the workspace"):
        mentioned_file_content_blocks(f"read @{secret}", base_dir=workspace)


def test_mentioned_file_content_blocks_reject_too_many_files(tmp_path: Path) -> None:
    mentions = []
    for index in range(9):
        name = f"note-{index}.md"
        (tmp_path / name).write_text(str(index), encoding="utf-8")
        mentions.append(f"@{name}")

    with pytest.raises(PromptPreparationError, match="Too many file mentions"):
        mentioned_file_content_blocks(" ".join(mentions), base_dir=tmp_path)


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

    with pytest.raises(PromptPreparationError, match="`devstral-latest`"):
        prepare_prompt(agent_loop, "look at @shot.png")


def test_prepare_prompt_rejection_names_the_display_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "shot.png").write_bytes(PNG_BYTES)
    agent_loop = build_test_agent_loop(
        config=_vision_config(
            supports_images=False, display_name="glm-5.2 (Mistral Hosted)"
        )
    )

    with pytest.raises(
        PromptPreparationError, match=r"`glm-5\.2 \(Mistral Hosted\)` does not support"
    ):
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


def test_read_untrusted_config_dirs_reports_dirs_and_settings_path(
    tmp_path: Path,
) -> None:
    vibe_dir = tmp_path / ".vibe"
    (vibe_dir / "tools").mkdir(parents=True)
    trust_store = TrustedFoldersManager()
    trust_store.add_trusted(tmp_path)
    trust_store.add_untrusted(vibe_dir)

    response = read_untrusted_config_dirs(tmp_path, trust_store)

    assert response.dirs == [str(vibe_dir.resolve())]
    assert response.settings_path == str(TRUSTED_FOLDERS_FILE.path)


def test_read_untrusted_config_dirs_empty_when_nothing_broken(tmp_path: Path) -> None:
    (tmp_path / ".vibe" / "tools").mkdir(parents=True)
    trust_store = TrustedFoldersManager()
    trust_store.add_trusted(tmp_path)

    response = read_untrusted_config_dirs(tmp_path, trust_store)

    assert response.dirs == []
