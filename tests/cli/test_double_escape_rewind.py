from __future__ import annotations

import pytest

from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer


@pytest.mark.asyncio
async def test_double_escape_on_empty_input_enters_rewind_mode(
    vibe_app: VibeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with vibe_app.run_test():
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = ""

        rewind_calls: list[bool] = []
        monkeypatch.setattr(
            vibe_app, "_start_rewind_mode", lambda **_: rewind_calls.append(True)
        )

        vibe_app._handle_input_double_escape()

        assert rewind_calls == [True]
        assert vibe_app._last_escape_time is None


@pytest.mark.asyncio
async def test_double_escape_with_content_clears_input_without_rewind(
    vibe_app: VibeApp, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with vibe_app.run_test():
        chat_input = vibe_app.query_one(ChatInputContainer)
        chat_input.value = "unsent draft"

        rewind_calls: list[bool] = []
        monkeypatch.setattr(
            vibe_app, "_start_rewind_mode", lambda **_: rewind_calls.append(True)
        )

        vibe_app._handle_input_double_escape()

        assert rewind_calls == []
        assert chat_input.value == ""
