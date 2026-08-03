from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tests.conftest import build_test_vibe_app
from vibe.app_server.models import AgentStatsSnapshot
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.context_progress import ContextProgress

_RESUMED_TOKENS = 50_000
_RESUMED_CONTEXT_WINDOW = 200_000


@pytest.fixture
def vibe_app() -> VibeApp:
    return build_test_vibe_app()


@pytest.mark.asyncio
async def test_resume_local_session_updates_context_progress(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        runtime = vibe_app.app_server.resources.runtime
        assert runtime.stats.context_tokens == 0

        def _apply_resumed_stats(*args: object) -> None:
            runtime._state.stats = AgentStatsSnapshot(
                context_tokens=_RESUMED_TOKENS, session_prompt_tokens=_RESUMED_TOKENS
            )
            runtime._state.context_window = _RESUMED_CONTEXT_WINDOW

        vibe_app.app_server.resume = AsyncMock(side_effect=_apply_resumed_stats)
        vibe_app._resume_history_from_messages = AsyncMock()
        vibe_app._mount_and_scroll = AsyncMock()

        await vibe_app._resume_local_session("abcd1234")

        widget = vibe_app.query_one(ContextProgress)
        assert widget.tokens.current_tokens == _RESUMED_TOKENS
        assert widget.tokens.max_tokens == _RESUMED_CONTEXT_WINDOW


@pytest.mark.asyncio
async def test_resume_local_session_shows_zero_when_no_llm_activity(
    vibe_app: VibeApp,
) -> None:
    async with vibe_app.run_test():
        vibe_app.app_server.resume = AsyncMock()
        vibe_app._resume_history_from_messages = AsyncMock()
        vibe_app._mount_and_scroll = AsyncMock()

        await vibe_app._resume_local_session("abcd1234")

        widget = vibe_app.query_one(ContextProgress)
        assert widget.tokens.current_tokens == 0
