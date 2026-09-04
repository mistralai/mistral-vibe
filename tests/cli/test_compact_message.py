from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_vibe_app
from vibe.app_server.models import AgentStatsSnapshot
from vibe.cli.textual_ui.widgets.compact import CompactMessage
from vibe.cli.textual_ui.widgets.context_progress import ContextProgress
from vibe.cli.textual_ui.widgets.messages import UserMessage

_SPENT_TOKENS = 120_000
_CONTEXT_WINDOW = 200_000


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


@pytest.mark.asyncio
async def test_compaction_empties_the_context_gauge() -> None:
    """*Prepare*: Fill the context, as a conversation long enough to compact would.
    *Do*: Compact.
    *Assert*: The gauge drops to what survived, without waiting for the next turn.
    """
    app = build_test_vibe_app()
    surviving_summary_tokens = 4_000

    async with app.run_test():
        # Prepare
        runtime_state = app.app_server.resources.runtime._state
        runtime_state.stats = AgentStatsSnapshot(
            context_tokens=_SPENT_TOKENS, session_prompt_tokens=_SPENT_TOKENS
        )
        runtime_state.context_window = _CONTEXT_WINDOW
        app._refresh_context_progress()
        assert app.query_one(ContextProgress).tokens.current_tokens == _SPENT_TOKENS

        def _shrink_to_summary(**_kwargs: object) -> str:
            # Stands in for the runtime read `AppServerSession.compact` performs,
            # which is pinned by `test_compact_refreshes_the_cached_context_gauge`.
            # This test owns the other half: the widget re-reads that cache rather
            # than waiting for the next `session/statsUpdated`.
            runtime_state.stats = AgentStatsSnapshot(
                context_tokens=surviving_summary_tokens,
                session_prompt_tokens=_SPENT_TOKENS,
            )
            return "summary"

        app.app_server.compact = AsyncMock(side_effect=_shrink_to_summary)

        # Do
        await app._run_compact(CompactMessage())

        # Assert
        assert (
            app.query_one(ContextProgress).tokens.current_tokens
            == surviving_summary_tokens
        )
