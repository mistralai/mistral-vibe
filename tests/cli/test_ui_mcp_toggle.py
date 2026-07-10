from __future__ import annotations

import pytest

from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.mcp_app import MCPApp, MCPSourceKind
from vibe.core.config import ConfigFileError, VibeConfig
from vibe.core.config.harness_files import get_harness_files_manager


def _corrupt_config_file() -> None:
    config_file = get_harness_files_manager().config_file
    assert config_file is not None
    config_file.write_text("[broken", encoding="utf-8")


def test_get_persisted_config_raises_config_file_error_on_invalid_toml() -> None:
    _corrupt_config_file()
    with pytest.raises(ConfigFileError):
        VibeConfig.get_persisted_config()


@pytest.mark.asyncio
async def test_mcp_toggle_notifies_instead_of_crashing_on_invalid_config(
    vibe_app: VibeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    notifications: list[str] = []

    def fake_notify(message: str, **_: object) -> None:
        notifications.append(message)

    async with vibe_app.run_test():
        monkeypatch.setattr(vibe_app, "notify", fake_notify)
        _corrupt_config_file()
        message = MCPApp.MCPToggled(
            name="some-connector", kind=MCPSourceKind.CONNECTOR, disabled=True
        )
        await vibe_app.on_mcpapp_mcptoggled(message)

    assert notifications
    assert "Invalid TOML" in notifications[0]
