from __future__ import annotations

import pytest

from vibe.cli.textual_ui.app import VibeApp


@pytest.mark.asyncio
async def test_inline_notice_show_displays_message(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        vibe_app._inline_notice.show("Selection copied to clipboard", timeout=10)
        await pilot.pause(0.05)

        assert vibe_app._inline_notice.display is True
