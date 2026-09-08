from __future__ import annotations

import pytest

from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.think import (
    Think,
    ThinkArgs,
    ThinkResult,
    ThinkToolConfig,
)
from vibe.core.types import ToolCallEvent, ToolResultEvent


@pytest.fixture
def think_tool(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = ThinkToolConfig()
    return Think(config_getter=lambda: config, state=BaseToolState())


async def _run(tool: Think, args: ThinkArgs) -> ThinkResult | None:
    result = None
    async for item in tool.run(args):
        result = item
    return result


@pytest.mark.asyncio
async def test_returns_thought_unchanged(think_tool):
    args = ThinkArgs(thought="I should read the config file first.")
    result = await _run(think_tool, args)

    assert result is not None
    assert result.thought == "I should read the config file first."


@pytest.mark.asyncio
async def test_raises_error_for_empty_thought(think_tool):
    args = ThinkArgs(thought="")
    with pytest.raises(ToolError, match="Empty thought"):
        async for _ in think_tool.run(args):
            pass


@pytest.mark.asyncio
async def test_raises_error_for_whitespace_only(think_tool):
    args = ThinkArgs(thought="   ")
    with pytest.raises(ToolError, match="Empty thought"):
        async for _ in think_tool.run(args):
            pass


@pytest.mark.asyncio
async def test_handles_long_thought(think_tool):
    long_thought = (
        "First, I need to understand the database schema.\n\n"
        "The users table has columns: id, name, email, created_at.\n"
        "The orders table references users via user_id.\n\n"
        "My plan:\n"
        "1. Add a migration for the new column\n"
        "2. Update the ORM model\n"
        "3. Write tests\n"
    )
    args = ThinkArgs(thought=long_thought)
    result = await _run(think_tool, args)

    assert result is not None
    assert result.thought == long_thought


@pytest.mark.asyncio
async def test_handles_special_characters(think_tool):
    thought = 'Code: `def foo(x: int) -> str:` and unicode: \u2603 \u00e9\u00e8\u00ea and "quotes"'
    args = ThinkArgs(thought=thought)
    result = await _run(think_tool, args)

    assert result is not None
    assert result.thought == thought


class TestToolUIDisplay:
    def test_call_display(self):
        args = ThinkArgs(thought="Some reasoning here")
        event = ToolCallEvent(
            tool_name="think", tool_class=Think, args=args, tool_call_id="123"
        )
        display = Think.get_call_display(event)
        assert display.summary == "Thinking..."

    def test_result_display_success(self):
        result = ThinkResult(thought="Some reasoning")
        event = ToolResultEvent(
            tool_name="think", tool_class=Think, result=result, tool_call_id="123"
        )
        display = Think.get_result_display(event)
        assert display.success is True
        assert display.message == "Done thinking"

    def test_result_display_error(self):
        event = ToolResultEvent(
            tool_name="think",
            tool_class=Think,
            result=None,
            error="Empty thought provided.",
            tool_call_id="123",
        )
        display = Think.get_result_display(event)
        assert display.success is False
        assert "Empty thought" in display.message

    def test_status_text(self):
        assert Think.get_status_text() == "Thinking"
