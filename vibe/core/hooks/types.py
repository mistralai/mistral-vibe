"""Type definitions for the hooks system.

Hooks allow users to run custom shell commands at specific points during
the agent's execution, similar to git hooks.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class HookEvent(StrEnum):
    """Events that can trigger hooks."""

    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    SESSION_START = "session_start"
    SESSION_END = "session_end"
    USER_PROMPT_SUBMIT = "user_prompt_submit"


class HookPermissionDecision(StrEnum):
    """Permission decisions that can be returned by PreToolUse hooks."""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


class HookConfig(BaseModel):
    """Configuration for a single hook.

    Attributes:
        type: The type of hook (currently only "command" is supported).
        command: The shell command to execute.
        timeout: Maximum execution time in seconds (default: 60).
        matcher: Regex pattern to match tool names (for PreToolUse/PostToolUse).
            Use "*" to match all tools, or a regex like "bash|read_file".
    """

    type: Literal["command"] = "command"
    command: str = Field(description="Shell command to execute")
    timeout: float = Field(default=60.0, ge=1.0, le=600.0)
    matcher: str = Field(
        default="*",
        description="Regex pattern to match tool names. Use '*' for all tools.",
    )


class HooksEventConfig(BaseModel):
    """Configuration for hooks grouped by event type."""

    PreToolUse: list[HookConfig] = Field(default_factory=list)
    PostToolUse: list[HookConfig] = Field(default_factory=list)
    SessionStart: list[HookConfig] = Field(default_factory=list)
    SessionEnd: list[HookConfig] = Field(default_factory=list)
    UserPromptSubmit: list[HookConfig] = Field(default_factory=list)


class HooksConfig(BaseModel):
    """Top-level hooks configuration."""

    enabled: bool = Field(default=True, description="Enable/disable all hooks")
    hooks: HooksEventConfig = Field(default_factory=HooksEventConfig)


class HookInput(BaseModel):
    """Base input passed to all hooks via stdin as JSON."""

    session_id: str
    cwd: str
    hook_event_name: str


class PreToolUseHookInput(HookInput):
    """Input for PreToolUse hooks."""

    tool_name: str
    tool_input: dict[str, Any]


class PostToolUseHookInput(HookInput):
    """Input for PostToolUse hooks."""

    tool_name: str
    tool_input: dict[str, Any]
    tool_result: str | None = None
    tool_error: str | None = None


class SessionStartHookInput(HookInput):
    """Input for SessionStart hooks."""

    pass


class SessionEndHookInput(HookInput):
    """Input for SessionEnd hooks."""

    message_count: int
    total_tokens: int


class UserPromptSubmitHookInput(HookInput):
    """Input for UserPromptSubmit hooks."""

    user_prompt: str


class HookOutput(BaseModel):
    """Base output returned by hooks via stdout as JSON.

    Attributes:
        continue_: Whether to continue execution (default: True).
        suppress_output: Whether to suppress hook output from logs (default: False).
        system_message: Optional message to inject into the conversation.
    """

    model_config = {"populate_by_name": True}

    continue_: bool = Field(default=True, alias="continue")
    suppress_output: bool = Field(default=False)
    system_message: str | None = None


class PreToolUseHookOutput(HookOutput):
    """Output specific to PreToolUse hooks.

    Attributes:
        permission_decision: Decision on whether to allow, deny, or ask for permission.
        updated_input: Optional modified tool input arguments.
        reason: Reason for the decision (shown to user/agent when blocking).
    """

    permission_decision: HookPermissionDecision = Field(
        default=HookPermissionDecision.ALLOW
    )
    updated_input: dict[str, Any] | None = None
    reason: str | None = None


class PostToolUseHookOutput(HookOutput):
    """Output specific to PostToolUse hooks."""

    pass


class SessionStartHookOutput(HookOutput):
    """Output specific to SessionStart hooks."""

    pass


class SessionEndHookOutput(HookOutput):
    """Output specific to SessionEnd hooks."""

    pass


class UserPromptSubmitHookOutput(HookOutput):
    """Output specific to UserPromptSubmit hooks.

    Attributes:
        modified_prompt: Optional modified user prompt.
    """

    modified_prompt: str | None = None


class HookExecutionError(Exception):
    """Raised when a hook fails to execute properly."""

    def __init__(
        self, hook_command: str, message: str, stderr: str | None = None
    ) -> None:
        self.hook_command = hook_command
        self.stderr = stderr
        super().__init__(f"Hook '{hook_command}' failed: {message}")


class HookResult(BaseModel):
    """Result of executing a hook."""

    hook_config: HookConfig
    output: HookOutput
    execution_time: float
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int = 0
    error: str | None = None
