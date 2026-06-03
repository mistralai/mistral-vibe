from __future__ import annotations

from typing import Any

import pytest
from textual.app import App
from textual.pilot import Pilot

from tests.snapshots.snap_compare import SnapCompare
from vibe.cli.textual_ui.widgets.banner.petit_chat import PetitChat
from vibe.core.config import ProviderConfig
from vibe.core.types import Backend
from vibe.setup.onboarding.screens.api_key import ApiKeyScreen


class StaticPetitChat(PetitChat):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(animate=False, **kwargs)


class ApiKeyScreenSnapshotApp(App[str | None]):
    CSS_PATH = "../../vibe/setup/onboarding/onboarding.tcss"

    def on_mount(self) -> None:
        self.theme = "ansi-dark"
        self.push_screen(
            ApiKeyScreen(
                ProviderConfig(
                    name="mistral",
                    api_base="https://api.mistral.ai/v1",
                    api_key_env_var="MISTRAL_API_KEY",
                    backend=Backend.MISTRAL,
                )
            )
        )


def test_snapshot_onboarding_api_key_with_valid_input(
    snap_compare: SnapCompare, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "vibe.setup.onboarding.screens.api_key.PetitChat", StaticPetitChat
    )

    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)
        await pilot.press(*"sk-test-api-key")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_onboarding_api_key.py:ApiKeyScreenSnapshotApp",
        terminal_size=(80, 24),
        run_before=run_before,
    )
