from __future__ import annotations

from pathlib import Path

import pytest
from textual.pilot import Pilot

from tests.conftest import build_test_vibe_config
from tests.mock.utils import mock_llm_chunk
from tests.snapshots.base_snapshot_test_app import BaseSnapshotTestApp, default_config
from tests.snapshots.snap_compare import SnapCompare
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.textual_ui.widgets.chat_input import paste_path
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfigSchema
from vibe.core.types import Backend

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
SNAP_ROOT = "/snap"


def _vision_config() -> VibeConfigSchema:
    base = default_config()
    models = [
        ModelConfig(
            name="mistral-vibe-cli-latest",
            provider="mistral",
            alias="devstral-latest",
            supports_images=True,
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
        disable_welcome_banner_animation=True,
        displayed_workdir=base.displayed_workdir,
        active_model="devstral-latest",
        models=models,
        providers=providers,
    )


@pytest.fixture(autouse=True)
def _accept_snap_paths_as_images(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_is_image_file(candidate: str) -> bool:
        return candidate.startswith(f"{SNAP_ROOT}/") and candidate.endswith((
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".webp",
        ))

    monkeypatch.setattr(paste_path, "_is_image_file", fake_is_image_file)


class _ImageAttachmentApp(BaseSnapshotTestApp):
    def __init__(self) -> None:
        super().__init__(
            config=_vision_config(),
            backend=FakeBackend(mock_llm_chunk(content="Got it.")),
        )


def test_snapshot_textbox_rewrites_typed_image_path(snap_compare: SnapCompare) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*f"look {SNAP_ROOT}/shot.png")
        await pilot.pause(0.2)

    assert snap_compare(
        "test_ui_snapshot_image_attachments.py:_ImageAttachmentApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_user_message_with_image_footer_singular(
    snap_compare: SnapCompare, tmp_working_directory: Path
) -> None:
    (tmp_working_directory / "shot.png").write_bytes(PNG_BYTES)

    async def run_before(pilot: Pilot) -> None:
        # Trailing space dismisses the @-mention autocomplete popup so that
        # `enter` submits the message instead of accepting a suggestion.
        await pilot.press(*"look @shot.png ")
        await pilot.press("enter")
        await pilot.pause(0.4)

    assert snap_compare(
        "test_ui_snapshot_image_attachments.py:_ImageAttachmentApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )


def test_snapshot_user_message_with_image_footer_plural(
    snap_compare: SnapCompare, tmp_working_directory: Path
) -> None:
    (tmp_working_directory / "a.png").write_bytes(PNG_BYTES)
    (tmp_working_directory / "b.png").write_bytes(PNG_BYTES)

    async def run_before(pilot: Pilot) -> None:
        await pilot.press(*"compare @a.png @b.png ")
        await pilot.press("enter")
        await pilot.pause(0.4)

    assert snap_compare(
        "test_ui_snapshot_image_attachments.py:_ImageAttachmentApp",
        terminal_size=(120, 36),
        run_before=run_before,
    )
