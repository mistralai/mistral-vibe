"""Tests for hooks executor module."""

from __future__ import annotations

import json

import pytest

from vibe.core.hooks.executor import execute_hook, execute_hooks_parallel
from vibe.core.hooks.types import (
    HookConfig,
    HookEvent,
    HookOutput,
    HookPermissionDecision,
    PreToolUseHookInput,
    PreToolUseHookOutput,
    SessionStartHookInput,
)


class TestExecuteHook:
    @pytest.mark.asyncio
    async def test_simple_echo_command(self) -> None:
        hook = HookConfig(command='echo \'{"continue": true}\'')
        hook_input = SessionStartHookInput(
            session_id="test-session",
            cwd="/tmp",
            hook_event_name=HookEvent.SESSION_START.value,
        )

        result = await execute_hook(hook, hook_input, "/tmp")

        assert result.exit_code == 0
        assert result.error is None
        assert result.output.continue_ is True

    @pytest.mark.asyncio
    async def test_hook_receives_json_input(self, tmp_path) -> None:
        script = tmp_path / "hook.sh"
        script.write_text("""#!/bin/bash
cat | python3 -c "import sys, json; data = json.load(sys.stdin); print(json.dumps({'received_tool': data.get('tool_name')}))"
""")
        script.chmod(0o755)

        hook = HookConfig(command=str(script))
        hook_input = PreToolUseHookInput(
            session_id="test-session",
            cwd=str(tmp_path),
            hook_event_name=HookEvent.PRE_TOOL_USE.value,
            tool_name="bash",
            tool_input={"command": "ls"},
        )

        result = await execute_hook(hook, hook_input, str(tmp_path))

        assert result.exit_code == 0
        output_data = json.loads(result.stdout or "{}")
        assert output_data.get("received_tool") == "bash"

    @pytest.mark.asyncio
    async def test_hook_deny_decision(self, tmp_path) -> None:
        script = tmp_path / "deny_hook.sh"
        script.write_text("""#!/bin/bash
echo '{"permission_decision": "deny", "reason": "Not allowed"}'
""")
        script.chmod(0o755)

        hook = HookConfig(command=str(script))
        hook_input = PreToolUseHookInput(
            session_id="test-session",
            cwd=str(tmp_path),
            hook_event_name=HookEvent.PRE_TOOL_USE.value,
            tool_name="bash",
            tool_input={"command": "rm -rf /"},
        )

        result = await execute_hook(hook, hook_input, str(tmp_path))

        assert result.exit_code == 0
        assert isinstance(result.output, PreToolUseHookOutput)
        assert result.output.permission_decision == HookPermissionDecision.DENY
        assert result.output.reason == "Not allowed"

    @pytest.mark.asyncio
    @pytest.mark.timeout(15)
    async def test_hook_timeout(self) -> None:
        hook = HookConfig(command="sleep 10", timeout=2.0)
        hook_input = SessionStartHookInput(
            session_id="test-session",
            cwd="/tmp",
            hook_event_name=HookEvent.SESSION_START.value,
        )

        result = await execute_hook(hook, hook_input, "/tmp")

        assert result.exit_code == -1
        assert "timed out" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_hook_exit_code_2_blocks(self) -> None:
        hook = HookConfig(command="exit 2")
        hook_input = SessionStartHookInput(
            session_id="test-session",
            cwd="/tmp",
            hook_event_name=HookEvent.SESSION_START.value,
        )

        result = await execute_hook(hook, hook_input, "/tmp")

        assert result.exit_code == 2
        assert result.output.continue_ is False

    @pytest.mark.asyncio
    async def test_hook_non_zero_exit_graceful(self) -> None:
        hook = HookConfig(command="exit 1")
        hook_input = SessionStartHookInput(
            session_id="test-session",
            cwd="/tmp",
            hook_event_name=HookEvent.SESSION_START.value,
        )

        result = await execute_hook(hook, hook_input, "/tmp")

        assert result.exit_code == 1
        assert result.output.continue_ is True

    @pytest.mark.asyncio
    async def test_hook_invalid_json_output(self) -> None:
        hook = HookConfig(command="echo 'not valid json'")
        hook_input = SessionStartHookInput(
            session_id="test-session",
            cwd="/tmp",
            hook_event_name=HookEvent.SESSION_START.value,
        )

        result = await execute_hook(hook, hook_input, "/tmp")

        assert result.exit_code == 0
        assert isinstance(result.output, HookOutput)
        assert result.output.continue_ is True

    @pytest.mark.asyncio
    async def test_hook_empty_output(self) -> None:
        hook = HookConfig(command="true")
        hook_input = SessionStartHookInput(
            session_id="test-session",
            cwd="/tmp",
            hook_event_name=HookEvent.SESSION_START.value,
        )

        result = await execute_hook(hook, hook_input, "/tmp")

        assert result.exit_code == 0
        assert result.output.continue_ is True

    @pytest.mark.asyncio
    async def test_hook_with_system_message(self) -> None:
        hook = HookConfig(
            command='echo \'{"system_message": "Warning: Be careful!"}\''
        )
        hook_input = PreToolUseHookInput(
            session_id="test-session",
            cwd="/tmp",
            hook_event_name=HookEvent.PRE_TOOL_USE.value,
            tool_name="bash",
            tool_input={"command": "rm file.txt"},
        )

        result = await execute_hook(hook, hook_input, "/tmp")

        assert result.output.system_message == "Warning: Be careful!"

    @pytest.mark.asyncio
    async def test_hook_updated_input(self, tmp_path) -> None:
        script = tmp_path / "modify_hook.sh"
        script.write_text("""#!/bin/bash
echo '{"updated_input": {"command": "ls -la"}}'
""")
        script.chmod(0o755)

        hook = HookConfig(command=str(script))
        hook_input = PreToolUseHookInput(
            session_id="test-session",
            cwd=str(tmp_path),
            hook_event_name=HookEvent.PRE_TOOL_USE.value,
            tool_name="bash",
            tool_input={"command": "ls"},
        )

        result = await execute_hook(hook, hook_input, str(tmp_path))

        assert isinstance(result.output, PreToolUseHookOutput)
        assert result.output.updated_input == {"command": "ls -la"}


class TestGetOutputClass:
    def test_returns_default_for_unknown_input(self) -> None:
        from vibe.core.hooks.executor import _get_output_class
        from vibe.core.hooks.types import HookInput, HookOutput

        class UnknownHookInput(HookInput):
            pass

        input_obj = UnknownHookInput(
            session_id="test",
            cwd="/tmp",
            hook_event_name="unknown",
        )
        output_class = _get_output_class(input_obj)
        assert output_class is HookOutput


class TestParseHookOutput:
    def test_validation_error_returns_default(self) -> None:
        from vibe.core.hooks.executor import _parse_hook_output

        output = _parse_hook_output(
            '{"permission_decision": "invalid_value"}',
            PreToolUseHookOutput,
        )
        assert output.permission_decision == HookPermissionDecision.ALLOW


class TestExecuteHooksParallel:
    @pytest.mark.asyncio
    async def test_multiple_hooks_parallel(self) -> None:
        hooks = [
            HookConfig(command='echo \'{"system_message": "Hook 1"}\''),
            HookConfig(command='echo \'{"system_message": "Hook 2"}\''),
            HookConfig(command='echo \'{"system_message": "Hook 3"}\''),
        ]
        hook_input = SessionStartHookInput(
            session_id="test-session",
            cwd="/tmp",
            hook_event_name=HookEvent.SESSION_START.value,
        )

        results = await execute_hooks_parallel(hooks, hook_input, "/tmp")

        assert len(results) == 3
        messages = [r.output.system_message for r in results]
        assert "Hook 1" in messages
        assert "Hook 2" in messages
        assert "Hook 3" in messages

    @pytest.mark.asyncio
    async def test_empty_hooks_list(self) -> None:
        hook_input = SessionStartHookInput(
            session_id="test-session",
            cwd="/tmp",
            hook_event_name=HookEvent.SESSION_START.value,
        )

        results = await execute_hooks_parallel([], hook_input, "/tmp")

        assert results == []

    @pytest.mark.asyncio
    async def test_one_failing_hook_doesnt_break_others(self) -> None:
        hooks = [
            HookConfig(command='echo \'{"system_message": "Success"}\''),
            HookConfig(command="exit 1"),
            HookConfig(command='echo \'{"system_message": "Also success"}\''),
        ]
        hook_input = SessionStartHookInput(
            session_id="test-session",
            cwd="/tmp",
            hook_event_name=HookEvent.SESSION_START.value,
        )

        results = await execute_hooks_parallel(hooks, hook_input, "/tmp")

        assert len(results) == 3
        success_count = sum(1 for r in results if r.error is None)
        assert success_count >= 2
