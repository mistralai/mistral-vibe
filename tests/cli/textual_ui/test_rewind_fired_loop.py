from __future__ import annotations

import pytest

from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.fired_loop import FiredLoop
from vibe.cli.textual_ui.widgets.messages import UserMessage


@pytest.mark.asyncio
async def test_rewind_skips_prompts_a_scheduled_loop_sent(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test() as pilot:
        await vibe_app._messages_area.mount_all([
            UserMessage("typed", history_entry_id="u0"),
            UserMessage("fired", history_entry_id="u1", fired_loop=FiredLoop("abc")),
        ])
        await pilot.pause()

        assert [
            widget.history_entry_id for widget in vibe_app._get_user_message_widgets()
        ] == ["u0"]
