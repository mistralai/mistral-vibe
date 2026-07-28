from __future__ import annotations

import pytest

from tests.conftest import (
    build_test_vibe_app,
    build_test_vibe_config,
    stub_config_reload,
)


@pytest.mark.asyncio
async def test_reload_config_picks_up_disk_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(
        config=build_test_vibe_config(autocopy_to_clipboard=False)
    )
    reloaded = build_test_vibe_config(autocopy_to_clipboard=True)
    stub_config_reload(monkeypatch, reloaded)

    async with app.run_test():
        assert app.config.autocopy_to_clipboard is False
        await app._reload_config()
        assert app.config.autocopy_to_clipboard is True
