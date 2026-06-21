from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel
import pytest

from tests.agent_loop.e2e.conftest import MistralAPI, build_e2e_agent_loop, e2e_config
from tests.backend.data.mistral import mistral_completion
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.tools.builtins.bash import BashResult
from vibe.core.tools.permissions import RequiredPermission
from vibe.core.types import ApprovalResponse, BaseEvent, ToolResultEvent


def _bash_call(command: str, timeout: int | None = None) -> dict[str, Any]:
    arguments: dict[str, Any] = {"command": command}
    if timeout is not None:
        arguments["timeout"] = timeout
    return mistral_completion(
        "",
        tool_calls=[
            {
                "id": "call_bash",
                "function": {"name": "bash", "arguments": json.dumps(arguments)},
                "index": 0,
            }
        ],
    )


async def _run_bash(
    mistral_api: MistralAPI,
    command: str,
    *,
    timeout: int | None = None,
    agent_name: str = BuiltinAgentName.AUTO_APPROVE,
    approval: ApprovalResponse | None = None,
) -> ToolResultEvent:
    mistral_api.reply(_bash_call(command, timeout), mistral_completion("done"))
    agent = build_e2e_agent_loop(
        config=e2e_config(enabled_tools=["bash"]), agent_name=agent_name
    )
    if approval is not None:

        async def approval_callback(
            _tool_name: str,
            _args: BaseModel,
            _tool_call_id: str,
            _rp: list[RequiredPermission] | None = None,
        ) -> tuple[ApprovalResponse, str | None]:
            return (approval, None)

        agent.set_approval_callback(approval_callback)

    events: list[BaseEvent] = [event async for event in agent.act("go")]
    return next(e for e in events if isinstance(e, ToolResultEvent))


@pytest.mark.asyncio
async def test_bash_captures_stdout(mistral_api: MistralAPI) -> None:
    result = await _run_bash(mistral_api, "echo hello")

    bash_result = cast(BashResult, result.result)
    assert bash_result.returncode == 0
    assert "hello" in bash_result.stdout


@pytest.mark.asyncio
async def test_bash_captures_stderr(mistral_api: MistralAPI) -> None:
    result = await _run_bash(mistral_api, "echo oops >&2")

    bash_result = cast(BashResult, result.result)
    assert "oops" in bash_result.stderr


@pytest.mark.asyncio
async def test_bash_nonzero_exit_surfaces_as_error(mistral_api: MistralAPI) -> None:
    result = await _run_bash(mistral_api, "exit 3")

    assert result.error is not None
    assert "Return code: 3" in result.error


@pytest.mark.asyncio
async def test_bash_timeout_surfaces_as_error(mistral_api: MistralAPI) -> None:
    result = await _run_bash(mistral_api, "sleep 5", timeout=1)

    assert result.error is not None
    assert "timed out" in result.error.lower()


@pytest.mark.asyncio
async def test_bash_output_truncated_to_max_bytes(mistral_api: MistralAPI) -> None:
    result = await _run_bash(mistral_api, "yes x | head -c 100000")

    bash_result = cast(BashResult, result.result)
    assert len(bash_result.stdout) <= 16_000


@pytest.mark.asyncio
async def test_bash_denylisted_command_is_skipped(mistral_api: MistralAPI) -> None:
    result = await _run_bash(
        mistral_api, "vim file.txt", agent_name=BuiltinAgentName.DEFAULT
    )

    assert result.skipped is True
    assert result.skip_reason is not None
    assert "denied" in result.skip_reason.lower()


@pytest.mark.asyncio
async def test_bash_allowlisted_command_runs_without_approval(
    mistral_api: MistralAPI,
) -> None:
    # No approval callback registered; an allowlisted command must run anyway.
    result = await _run_bash(
        mistral_api, "echo allowed", agent_name=BuiltinAgentName.DEFAULT
    )

    assert result.skipped is False
    assert "allowed" in cast(BashResult, result.result).stdout


@pytest.mark.asyncio
async def test_bash_non_allowlisted_command_requires_approval(
    mistral_api: MistralAPI,
) -> None:
    result = await _run_bash(
        mistral_api,
        "touch newfile.txt",
        agent_name=BuiltinAgentName.DEFAULT,
        approval=ApprovalResponse.YES,
    )

    assert result.skipped is False
    assert (Path.cwd() / "newfile.txt").exists()


@pytest.mark.asyncio
async def test_bash_non_allowlisted_command_denied_at_prompt_is_skipped(
    mistral_api: MistralAPI,
) -> None:
    result = await _run_bash(
        mistral_api,
        "touch denied.txt",
        agent_name=BuiltinAgentName.DEFAULT,
        approval=ApprovalResponse.NO,
    )

    assert result.skipped is True
    assert not (Path.cwd() / "denied.txt").exists()


@pytest.mark.asyncio
async def test_bash_command_touching_outside_workdir_requires_approval(
    mistral_api: MistralAPI, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    result = await _run_bash(
        mistral_api,
        f"touch {outside}",
        agent_name=BuiltinAgentName.DEFAULT,
        approval=ApprovalResponse.NO,
    )

    assert result.skipped is True
    assert not outside.exists()
