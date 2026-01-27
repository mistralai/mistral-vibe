from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.cli.update_notifier.update import do_update


@pytest.mark.asyncio
async def test_do_update_returns_true_when_first_command_succeeds() -> None:
    mock_process = MagicMock()
    mock_process.wait = AsyncMock(return_value=None)
    mock_process.returncode = 0

    with patch(
        "vibe.cli.update_notifier.update.UPDATE_COMMANDS", ["command_1", "command_2"]
    ):
        with patch(
            "vibe.cli.update_notifier.update.asyncio.create_subprocess_shell"
        ) as mock_create:
            mock_create.return_value = mock_process

            result = await do_update()

            assert result is True
            mock_create.assert_called_once()
            assert "command_1" in mock_create.call_args[0][0]


@pytest.mark.asyncio
async def test_do_update_returns_true_when_second_command_succeeds() -> None:
    mock_process_fail = MagicMock()
    mock_process_fail.wait = AsyncMock(return_value=None)
    mock_process_fail.returncode = 1

    mock_process_success = MagicMock()
    mock_process_success.wait = AsyncMock(return_value=None)
    mock_process_success.returncode = 0

    with patch(
        "vibe.cli.update_notifier.update.UPDATE_COMMANDS", ["command_1", "command_2"]
    ):
        with patch(
            "vibe.cli.update_notifier.update.asyncio.create_subprocess_shell"
        ) as mock_create:
            mock_create.side_effect = [mock_process_fail, mock_process_success]

            result = await do_update()

            assert result is True
            assert mock_create.call_count == 2
            assert "command_1" in mock_create.call_args_list[0][0][0]
            assert "command_2" in mock_create.call_args_list[1][0][0]


@pytest.mark.asyncio
async def test_do_update_returns_false_when_all_commands_fail() -> None:
    mock_process = MagicMock()
    mock_process.wait = AsyncMock(return_value=None)
    mock_process.returncode = 1

    with patch(
        "vibe.cli.update_notifier.update.UPDATE_COMMANDS", ["command_1", "command_2"]
    ):
        with patch(
            "vibe.cli.update_notifier.update.asyncio.create_subprocess_shell"
        ) as mock_create:
            mock_create.return_value = mock_process

            result = await do_update()

            assert result is False
            assert mock_create.call_count == 2
