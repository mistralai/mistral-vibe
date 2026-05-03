from __future__ import annotations

from unittest.mock import patch

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.textual_ui.widgets.messages import AssistantMessage


@pytest.mark.asyncio
async def test_copy_command_copies_last_assistant_message() -> None:
    app = build_test_vibe_app()
    second_reply = "\n```python\nprint('second reply')\n```\n"
    stripped_second_reply = second_reply.strip()
    async with app.run_test() as pilot:
        await app._mount_and_scroll(AssistantMessage("first reply"))
        await app._mount_and_scroll(AssistantMessage(second_reply))
        with (
            patch(
                "vibe.cli.textual_ui.app.copy_text_to_clipboard",
                return_value=stripped_second_reply,
            ) as mock_copy,
            patch.object(
                app.agent_loop.telemetry_client, "send_user_copied_text"
            ) as mock_telemetry,
        ):
            handled = await app._handle_command("/copy")
        await pilot.pause()
    assert handled is True
    assert stripped_second_reply != second_reply
    mock_copy.assert_called_once_with(
        app,
        stripped_second_reply,
        success_message="Last agent message copied to clipboard",
    )
    mock_telemetry.assert_called_once_with(stripped_second_reply)


@pytest.mark.asyncio
async def test_copy_command_warns_when_no_assistant_message() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        with patch.object(app, "notify") as mock_notify:
            handled = await app._handle_command("/copy")

        await pilot.pause()

    assert handled is True
    mock_notify.assert_called_once_with(
        "No agent message available to copy", severity="warning", timeout=3
    )
