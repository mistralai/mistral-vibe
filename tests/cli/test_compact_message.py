from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.messages import UserMessage


class TestCompactMessage:
    def test_get_content_after_compaction(self) -> None:
        message = CompactMessage()

        message.set_complete()

        assert message.get_content() == "Compaction completed."


@pytest.mark.asyncio
async def test_completed_compaction_keeps_visible_history() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        earlier_message = UserMessage("Before compaction")
        compact_message = CompactMessage()
        await app._mount_and_scroll(earlier_message)
        await app._mount_and_scroll(compact_message)

        compact_message.set_complete()
        await pilot.pause()

        assert earlier_message.parent is app._messages_area
