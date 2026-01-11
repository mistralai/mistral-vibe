"""Tests for hooks type definitions."""

from __future__ import annotations

import pytest

from vibe.core.hooks.types import (
    HookConfig,
    HookEvent,
    HookOutput,
    HookPermissionDecision,
    HooksConfig,
    HooksEventConfig,
    PostToolUseHookInput,
    PreToolUseHookInput,
    PreToolUseHookOutput,
    SessionEndHookInput,
    SessionStartHookInput,
    UserPromptSubmitHookInput,
    UserPromptSubmitHookOutput,
)


class TestHookEvent:
    def test_event_values(self) -> None:
        assert HookEvent.PRE_TOOL_USE.value == "pre_tool_use"
        assert HookEvent.POST_TOOL_USE.value == "post_tool_use"
        assert HookEvent.SESSION_START.value == "session_start"
        assert HookEvent.SESSION_END.value == "session_end"
        assert HookEvent.USER_PROMPT_SUBMIT.value == "user_prompt_submit"


class TestHookPermissionDecision:
    def test_permission_values(self) -> None:
        assert HookPermissionDecision.ALLOW.value == "allow"
        assert HookPermissionDecision.DENY.value == "deny"
        assert HookPermissionDecision.ASK.value == "ask"


class TestHookConfig:
    def test_defaults(self) -> None:
        hook = HookConfig(command="echo test")
        assert hook.type == "command"
        assert hook.command == "echo test"
        assert hook.timeout == 60.0
        assert hook.matcher == "*"

    def test_custom_values(self) -> None:
        hook = HookConfig(
            command="python script.py",
            timeout=30.0,
            matcher="bash|read_file",
        )
        assert hook.command == "python script.py"
        assert hook.timeout == 30.0
        assert hook.matcher == "bash|read_file"

    def test_timeout_bounds(self) -> None:
        with pytest.raises(ValueError):
            HookConfig(command="test", timeout=0.5)

        with pytest.raises(ValueError):
            HookConfig(command="test", timeout=700.0)


class TestHooksConfig:
    def test_defaults(self) -> None:
        config = HooksConfig()
        assert config.enabled is True
        assert isinstance(config.hooks, HooksEventConfig)

    def test_disabled(self) -> None:
        config = HooksConfig(enabled=False)
        assert config.enabled is False


class TestHooksEventConfig:
    def test_defaults(self) -> None:
        config = HooksEventConfig()
        assert config.PreToolUse == []
        assert config.PostToolUse == []
        assert config.SessionStart == []
        assert config.SessionEnd == []
        assert config.UserPromptSubmit == []

    def test_with_hooks(self) -> None:
        hook = HookConfig(command="test")
        config = HooksEventConfig(PreToolUse=[hook])
        assert len(config.PreToolUse) == 1
        assert config.PreToolUse[0].command == "test"


class TestHookOutput:
    def test_defaults(self) -> None:
        output = HookOutput()
        assert output.continue_ is True
        assert output.suppress_output is False
        assert output.system_message is None

    def test_continue_alias(self) -> None:
        data = {"continue": False, "systemMessage": "test"}
        output = HookOutput.model_validate(data)
        assert output.continue_ is False

    def test_with_system_message(self) -> None:
        output = HookOutput(system_message="Hello from hook")
        assert output.system_message == "Hello from hook"


class TestPreToolUseHookInput:
    def test_creation(self) -> None:
        input_data = PreToolUseHookInput(
            session_id="sess-123",
            cwd="/home/user/project",
            hook_event_name="pre_tool_use",
            tool_name="bash",
            tool_input={"command": "ls -la"},
        )
        assert input_data.session_id == "sess-123"
        assert input_data.cwd == "/home/user/project"
        assert input_data.hook_event_name == "pre_tool_use"
        assert input_data.tool_name == "bash"
        assert input_data.tool_input == {"command": "ls -la"}


class TestPreToolUseHookOutput:
    def test_defaults(self) -> None:
        output = PreToolUseHookOutput()
        assert output.permission_decision == HookPermissionDecision.ALLOW
        assert output.updated_input is None
        assert output.reason is None

    def test_deny(self) -> None:
        output = PreToolUseHookOutput(
            permission_decision=HookPermissionDecision.DENY,
            reason="Command not allowed",
        )
        assert output.permission_decision == HookPermissionDecision.DENY
        assert output.reason == "Command not allowed"

    def test_updated_input(self) -> None:
        output = PreToolUseHookOutput(
            updated_input={"command": "ls -l"}
        )
        assert output.updated_input == {"command": "ls -l"}


class TestPostToolUseHookInput:
    def test_with_result(self) -> None:
        input_data = PostToolUseHookInput(
            session_id="sess-123",
            cwd="/home/user",
            hook_event_name="post_tool_use",
            tool_name="bash",
            tool_input={"command": "ls"},
            tool_result="file1.txt\nfile2.txt",
        )
        assert input_data.tool_result == "file1.txt\nfile2.txt"
        assert input_data.tool_error is None

    def test_with_error(self) -> None:
        input_data = PostToolUseHookInput(
            session_id="sess-123",
            cwd="/home/user",
            hook_event_name="post_tool_use",
            tool_name="bash",
            tool_input={"command": "invalid"},
            tool_error="Command not found",
        )
        assert input_data.tool_result is None
        assert input_data.tool_error == "Command not found"


class TestSessionStartHookInput:
    def test_creation(self) -> None:
        input_data = SessionStartHookInput(
            session_id="sess-123",
            cwd="/home/user",
            hook_event_name="session_start",
        )
        assert input_data.session_id == "sess-123"


class TestSessionEndHookInput:
    def test_creation(self) -> None:
        input_data = SessionEndHookInput(
            session_id="sess-123",
            cwd="/home/user",
            hook_event_name="session_end",
            message_count=10,
            total_tokens=5000,
        )
        assert input_data.message_count == 10
        assert input_data.total_tokens == 5000


class TestUserPromptSubmitHookInput:
    def test_creation(self) -> None:
        input_data = UserPromptSubmitHookInput(
            session_id="sess-123",
            cwd="/home/user",
            hook_event_name="user_prompt_submit",
            user_prompt="Hello, help me with Python",
        )
        assert input_data.user_prompt == "Hello, help me with Python"


class TestUserPromptSubmitHookOutput:
    def test_defaults(self) -> None:
        output = UserPromptSubmitHookOutput()
        assert output.modified_prompt is None

    def test_modified_prompt(self) -> None:
        output = UserPromptSubmitHookOutput(
            modified_prompt="Modified: Hello, help me with Python"
        )
        assert output.modified_prompt == "Modified: Hello, help me with Python"


class TestHookExecutionError:
    def test_error_creation(self) -> None:
        from vibe.core.hooks.types import HookExecutionError

        error = HookExecutionError(
            hook_command="python script.py",
            message="Script failed",
            stderr="Error output",
        )
        assert error.hook_command == "python script.py"
        assert error.stderr == "Error output"
        assert "python script.py" in str(error)
        assert "Script failed" in str(error)

    def test_error_without_stderr(self) -> None:
        from vibe.core.hooks.types import HookExecutionError

        error = HookExecutionError(
            hook_command="test.sh",
            message="Failed",
        )
        assert error.stderr is None
