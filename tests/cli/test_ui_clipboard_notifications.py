from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from textual.selection import Selection
from textual.widget import Widget

from vibe.cli.clipboard import copy_selection_to_clipboard
from vibe.cli.textual_ui.app import VibeApp
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig, VibeConfig


class ClipboardSelectionWidget(Widget):
    def __init__(self, selected_text: str) -> None:
        super().__init__()
        self._selected_text = selected_text

    @property
    def text_selection(self) -> Selection | None:
        return Selection(None, None)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        return (self._selected_text, "\n")


@pytest.mark.asyncio
async def test_ui_clipboard_notification_does_not_crash_on_markup_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_loop = AgentLoop(
        config=VibeConfig(
            session_logging=SessionLoggingConfig(enabled=False),
            enable_update_checks=False,
        )
    )
    app = VibeApp(agent_loop=agent_loop)

    async with app.run_test(notifications=True) as pilot:
        await app.mount(ClipboardSelectionWidget("[/]"))
        with patch("vibe.cli.clipboard._get_copy_fns") as mock_get_copy_fns:
            mock_get_copy_fns.return_value = [MagicMock()]
            copy_selection_to_clipboard(app)

        await pilot.pause(0.1)
        notifications = list(app._notifications)
        assert notifications
        notification = notifications[-1]
        assert notification.markup is False
        assert "copied to clipboard" in notification.message
