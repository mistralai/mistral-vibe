from __future__ import annotations

from pathlib import Path

import pytest

from vibe.app_server._shell import ShellController, manual_shell_context
from vibe.app_server.protocol import ShellRunParams, ShellRunResponse


def test_manual_shell_context_caps_stdout_and_stderr_independently() -> None:
    result = ShellRunResponse(
        operation_id="shell-1",
        command="build",
        cwd="/workspace",
        stdout="o" * 10,
        stderr="e" * 10,
        exit_code=1,
    )

    context = manual_shell_context(result, max_output_bytes=5)

    assert context.count("[truncated]") == 2
    assert "oooooo" not in context
    assert "eeeeee" not in context


@pytest.mark.asyncio
async def test_shell_interrupt_is_sticky_before_process_spawn(tmp_path: Path) -> None:
    controller = ShellController(tmp_path)
    operation_id = "shell-early-interrupt"

    async def interrupt_on_start() -> None:
        assert await controller.interrupt(operation_id)

    result = await controller.run(
        ShellRunParams(
            session_id="session-1",
            operation_id=operation_id,
            command="sleep 10",
            cwd=str(tmp_path),
        ),
        observe_start=interrupt_on_start,
    )

    assert result.interrupted is True
