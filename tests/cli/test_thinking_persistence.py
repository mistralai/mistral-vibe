from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config_schema
from vibe.cli.textual_ui.widgets.thinking_picker import ThinkingPickerApp
from vibe.core.config import ModelConfig


def _persisted_thinking(config_dir: Path, alias: str) -> str | None:
    with (config_dir / "config.toml").open("rb") as file:
        data = tomllib.load(file)
    for model in data.get("models", []):
        if model.get("alias", model.get("name")) == alias:
            return model.get("thinking")
    return None


@pytest.mark.asyncio
async def test_thinking_change_survives_later_config_change(config_dir: Path) -> None:
    config = build_test_vibe_config_schema(
        active_model="devstral-latest",
        models=[
            ModelConfig(
                name="mistral-vibe-cli-latest",
                provider="mistral",
                alias="devstral-latest",
            )
        ],
    )
    app = build_test_vibe_app(config=config)

    async with app.run_test() as pilot:
        await app.on_thinking_picker_app_thinking_selected(
            ThinkingPickerApp.ThinkingSelected("high")
        )
        # The selection persists via the main queue (two-phase execution), so
        # wait for the drain to finish before the next write — otherwise the
        # thinking level is read before it lands on disk.
        for _ in range(100):
            if not app._queue.draining:
                break
            await pilot.pause(0.01)
        # A subsequent /config change must not clobber the thinking level.
        await app._persist_config_changes({"autocopy_to_clipboard": False})

    assert _persisted_thinking(config_dir, "devstral-latest") == "high"
    with (config_dir / "config.toml").open("rb") as file:
        assert tomllib.load(file)["autocopy_to_clipboard"] is False
