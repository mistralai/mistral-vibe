from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest

from tests.agent_loop.e2e.conftest import MistralAPI, build_e2e_agent_loop, e2e_config
from tests.backend.data.mistral import mistral_completion
from vibe.core.tools.builtins.grep import GrepResult
from vibe.core.tools.builtins.read import ReadResult
from vibe.core.tools.builtins.todo import TodoResult
from vibe.core.types import BaseEvent, ToolResultEvent


def _tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return mistral_completion(
        "",
        tool_calls=[
            {
                "id": f"call_{name}",
                "function": {"name": name, "arguments": json.dumps(arguments)},
                "index": 0,
            }
        ],
    )


async def _run_tool(
    mistral_api: MistralAPI, name: str, arguments: dict[str, Any]
) -> ToolResultEvent:
    # Drive one tool call against the real cwd, then a final reply, and return
    # the single tool result for the test to assert on.
    mistral_api.reply(_tool_call(name, arguments), mistral_completion("done"))
    agent = build_e2e_agent_loop(config=e2e_config(enabled_tools=[name]))
    events: list[BaseEvent] = [event async for event in agent.act("go")]
    result = next(e for e in events if isinstance(e, ToolResultEvent))
    assert result.error is None
    return result


@pytest.mark.asyncio
async def test_write_file_tool_creates_file(mistral_api: MistralAPI) -> None:
    target = Path.cwd() / "note.txt"

    await _run_tool(mistral_api, "write_file", {"path": str(target), "content": "hi\n"})

    assert target.read_text() == "hi\n"


@pytest.mark.asyncio
async def test_read_tool_returns_file_content(mistral_api: MistralAPI) -> None:
    target = Path.cwd() / "note.txt"
    target.write_text("hello world\n")

    result = await _run_tool(mistral_api, "read", {"file_path": str(target)})

    assert "hello world" in cast(ReadResult, result.result).content


@pytest.mark.asyncio
async def test_edit_tool_replaces_text(mistral_api: MistralAPI) -> None:
    target = Path.cwd() / "note.txt"
    target.write_text("hello world\n")

    await _run_tool(
        mistral_api,
        "edit",
        {"file_path": str(target), "old_string": "hello", "new_string": "goodbye"},
    )

    assert target.read_text() == "goodbye world\n"


@pytest.mark.asyncio
async def test_grep_tool_finds_matches(mistral_api: MistralAPI) -> None:
    (Path.cwd() / "note.txt").write_text("needle here\n")

    result = await _run_tool(mistral_api, "grep", {"pattern": "needle", "path": "."})

    assert cast(GrepResult, result.result).match_count >= 1


@pytest.mark.asyncio
async def test_todo_tool_writes_items(mistral_api: MistralAPI) -> None:
    result = await _run_tool(
        mistral_api,
        "todo",
        {"action": "write", "todos": [{"id": "1", "content": "ship it"}]},
    )

    assert cast(TodoResult, result.result).total_count == 1
