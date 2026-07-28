from __future__ import annotations

import time

import pytest

from tests.conftest import build_test_agent_loop
from tests.stubs.app_server import create_test_app_server_session
from vibe.app_server._projection import project_history
from vibe.app_server._shell_requests import _manual_shell_context
from vibe.app_server.events import HistoryEntryAdded, HistoryEntryUpdated
from vibe.app_server.models import CompletedEffectState, PublicEffectEntry
from vibe.app_server.protocol import ShellRunResponse
from vibe.core.types import Role
from vibe.utils.tool_presentation import ToolEffectKind


def test_manual_shell_context_caps_stdout_and_stderr_independently() -> None:
    result = ShellRunResponse(
        operation_id="shell-1",
        command="build",
        cwd="/workspace",
        stdout="o" * 10,
        stderr="e" * 10,
        exit_code=1,
    )

    context = _manual_shell_context(result, max_output_bytes=5)

    assert context.count("[truncated]") == 2
    assert "oooooo" not in context
    assert "eeeeee" not in context


@pytest.mark.asyncio
async def test_shell_is_one_public_effect_and_injects_model_context() -> None:
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)

    try:
        events = [
            event
            async for event in session.resources.shell.run(
                "printf 'hello'; printf 'warning' >&2"
            )
        ]
    finally:
        await session.close()

    result = next(event for event in events if isinstance(event, ShellRunResponse))
    assert result.stdout == "hello"
    assert result.stderr == "warning"
    assert result.exit_code == 0

    added = next(event for event in events if isinstance(event, HistoryEntryAdded))
    assert isinstance(added.entry, PublicEffectEntry)
    assert added.entry.detail.kind is ToolEffectKind.SHELL
    updates = [event for event in events if isinstance(event, HistoryEntryUpdated)]
    assert updates
    assert all(update.entry.id == added.entry.id for update in updates)
    final = updates[-1].entry
    assert isinstance(final, PublicEffectEntry)
    assert isinstance(final.state, CompletedEffectState)
    assert final.state.output == {"stdout": "hello", "stderr": "warning"}
    assert any(entry.id == final.id for entry in session.history)

    injected = agent_loop.messages[-1]
    assert injected.role is Role.user
    assert injected.injected is True
    assert injected.content is not None
    assert "Manual `!` command result from the user." in injected.content
    assert "Command: `printf 'hello'; printf 'warning' >&2`" in injected.content
    assert "Stdout:\n```text\nhello\n```" in injected.content
    assert "Stderr:\n```text\nwarning\n```" in injected.content

    restored = next(
        entry
        for entry in project_history(agent_loop)
        if isinstance(entry, PublicEffectEntry) and entry.id == final.id
    )
    assert restored.detail == final.detail
    assert restored.state == final.state


@pytest.mark.asyncio
async def test_shell_timeout_terminates_process() -> None:
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)
    started_at = time.monotonic()

    try:
        events = [
            event
            async for event in session.resources.shell.run(
                "sleep 10", timeout_seconds=0.01
            )
        ]
    finally:
        await session.close()

    result = next(event for event in events if isinstance(event, ShellRunResponse))
    assert result.timed_out is True
    assert result.interrupted is False
    assert result.exit_code != 0
    assert time.monotonic() - started_at < 2


@pytest.mark.asyncio
async def test_closing_shell_stream_interrupts_process_and_allows_next_command() -> (
    None
):
    agent_loop = build_test_agent_loop()
    session = await create_test_app_server_session(agent_loop)
    stream = session.resources.shell.run("printf 'started'; sleep 10")

    try:
        events = []
        while not any(isinstance(event, HistoryEntryUpdated) for event in events):
            events.append(await anext(stream))
        await stream.aclose()

        events = [
            event async for event in session.resources.shell.run("printf 'finished'")
        ]
    finally:
        await stream.aclose()
        await session.close()

    result = next(event for event in events if isinstance(event, ShellRunResponse))
    assert result.stdout == "finished"
    assert result.exit_code == 0
