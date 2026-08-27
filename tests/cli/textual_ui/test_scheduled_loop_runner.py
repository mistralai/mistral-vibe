from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from vibe.app_server.models import ScheduledLoop
from vibe.cli.textual_ui.scheduled_loop_runner import (
    ScheduledLoopCommands,
    _format_duration,
)
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage


def _loop(loop_id: str = "loop-1") -> ScheduledLoop:
    return ScheduledLoop(
        id=loop_id, prompt="prompt", interval_seconds=30, next_fire_at=60
    )


@pytest.mark.parametrize(
    ("seconds", "formatted"), [(90, "1m30s"), (180, "3m"), (7200, "2h"), (259200, "3d")]
)
def test_loop_duration_formatting(seconds: int, formatted: str) -> None:
    assert _format_duration(seconds) == formatted


@pytest.mark.asyncio
async def test_loop_command_routes_to_typed_methods() -> None:
    loops = AsyncMock()
    loops.list.return_value = [_loop()]
    loops.create.return_value = _loop("created")
    loops.delete.return_value = _loop("deleted")
    loops.clear.return_value = 2
    commands = ScheduledLoopCommands(loops, tools_collapsed=lambda: False)

    assert isinstance(await commands.handle_command("list"), UserCommandMessage)
    assert isinstance(await commands.handle_command("30s prompt"), UserCommandMessage)
    assert isinstance(
        await commands.handle_command("cancel deleted"), UserCommandMessage
    )
    assert isinstance(await commands.handle_command("cancel all"), UserCommandMessage)

    loops.list.assert_awaited_once_with()
    loops.create.assert_awaited_once_with("30s", "prompt")
    loops.delete.assert_awaited_once_with("deleted")
    loops.clear.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_loop_command_rejects_missing_delete_target_locally() -> None:
    loops = AsyncMock()
    commands = ScheduledLoopCommands(loops, tools_collapsed=lambda: True)

    result = await commands.handle_command("cancel")

    assert isinstance(result, ErrorMessage)
    loops.delete.assert_not_awaited()
    loops.clear.assert_not_awaited()
