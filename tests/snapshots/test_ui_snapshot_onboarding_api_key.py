from __future__ import annotations

from textual.app import App
from textual.pilot import Pilot

from tests.snapshots.snap_compare import SnapCompare
from vibe.core.config import ProviderConfig
from vibe.core.types import Backend
from vibe.setup.onboarding.screens.api_key import ApiKeyScreen


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
    snap_compare: SnapCompare,
) -> None:
    async def run_before(pilot: Pilot) -> None:
        await pilot.pause(0.2)
        await pilot.press(*"sk-test-api-key")
        await pilot.pause(0.1)

    assert snap_compare(
        "test_ui_snapshot_onboarding_api_key.py:ApiKeyScreenSnapshotApp",
        terminal_size=(80, 24),
        run_before=run_before,
    )
