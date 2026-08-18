from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

import pytest
from textual.widgets import Static

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.widgets.messages import CustomToolsDeprecationMessage


async def _wait_for(predicate: Callable[[], bool], pilot) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 1
    while loop.time() < deadline:
        if predicate():
            return
        await pilot.pause(0.05)
    pytest.fail("Condition not met within timeout")


@pytest.mark.asyncio
async def test_startup_warns_when_custom_tools_are_available(tmp_path: Path) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "weather.py").write_text("""
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from pydantic import BaseModel

class WeatherArgs(BaseModel):
    pass

class WeatherResult(BaseModel):
    pass

class WeatherTool(BaseTool[WeatherArgs, WeatherResult, BaseToolConfig, BaseToolState]):
    async def run(self, args, ctx=None):
        yield WeatherResult()
""")
    config = build_test_vibe_config(tool_paths=[tools_dir])
    app = build_test_vibe_app(config=config)

    async with app.run_test() as pilot:
        await _wait_for(lambda: bool(app.query(CustomToolsDeprecationMessage)), pilot)
        message = app.query_one(CustomToolsDeprecationMessage)
        later_message = Static("Later message")
        await app._mount_and_scroll(later_message)
        messages_area = app.query_one("#messages")

        assert message.parent is messages_area
        assert messages_area.children[-1] is later_message

    assert "Support for custom tools will be deprecated soon." in message._content
    assert (
        "Ask Vibe to help replace yours (`weather_tool`) with a skill."
        in message._content
    )


@pytest.mark.asyncio
async def test_startup_does_not_warn_without_custom_tools() -> None:
    app = build_test_vibe_app()

    async with app.run_test() as pilot:
        await pilot.pause(0.4)

    assert not app.query(CustomToolsDeprecationMessage)


@pytest.mark.asyncio
async def test_rebuilding_the_transcript_restores_the_custom_tools_warning(
    tmp_path: Path,
) -> None:
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "weather.py").write_text("""
from vibe.core.tools.base import BaseTool, BaseToolConfig, BaseToolState
from pydantic import BaseModel

class WeatherArgs(BaseModel):
    pass

class WeatherResult(BaseModel):
    pass

class WeatherTool(BaseTool[WeatherArgs, WeatherResult, BaseToolConfig, BaseToolState]):
    async def run(self, args, ctx=None):
        yield WeatherResult()
""")
    app = build_test_vibe_app(config=build_test_vibe_config(tool_paths=[tools_dir]))

    async with app.run_test() as pilot:
        await _wait_for(lambda: bool(app.query(CustomToolsDeprecationMessage)), pilot)
        await app._rebuild_transcript_from_current_session()

        messages = app.query(CustomToolsDeprecationMessage)
        assert len(messages) == 1
        assert messages[0].parent is app.query_one("#messages")
