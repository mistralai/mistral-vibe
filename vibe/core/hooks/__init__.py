"""Hooks system for Vibe.

This module provides a hooks system that allows users to run custom
shell commands at specific points during the agent's execution.

Example configuration in config.toml:

    [hooks]
    enabled = true

    [[hooks.hooks.PreToolUse]]
    type = "command"
    command = "python3 ~/.vibe/hooks/validate_tool.py"
    matcher = "bash"
    timeout = 10

    [[hooks.hooks.PostToolUse]]
    type = "command"
    command = "python3 ~/.vibe/hooks/log_tool_use.py"
    matcher = "*"

    [[hooks.hooks.SessionStart]]
    type = "command"
    command = "echo 'Session started'"

Hook scripts receive JSON input via stdin and should output JSON to stdout.
See the types module for the input/output schemas for each hook type.
"""
from __future__ import annotations

from vibe.core.hooks.manager import (
    HookManager,
    PostToolUseResult,
    PreToolUseResult,
    UserPromptSubmitResult,
)
from vibe.core.hooks.types import (
    HookConfig,
    HookEvent,
    HookExecutionError,
    HookInput,
    HookOutput,
    HookPermissionDecision,
    HookResult,
    HooksConfig,
    HooksEventConfig,
    PostToolUseHookInput,
    PostToolUseHookOutput,
    PreToolUseHookInput,
    PreToolUseHookOutput,
    SessionEndHookInput,
    SessionEndHookOutput,
    SessionStartHookInput,
    SessionStartHookOutput,
    UserPromptSubmitHookInput,
    UserPromptSubmitHookOutput,
)

__all__ = [
    "HookConfig",
    "HookEvent",
    "HookExecutionError",
    "HookInput",
    "HookManager",
    "HookOutput",
    "HookPermissionDecision",
    "HookResult",
    "HooksConfig",
    "HooksEventConfig",
    "PostToolUseHookInput",
    "PostToolUseHookOutput",
    "PostToolUseResult",
    "PreToolUseHookInput",
    "PreToolUseHookOutput",
    "PreToolUseResult",
    "SessionEndHookInput",
    "SessionEndHookOutput",
    "SessionStartHookInput",
    "SessionStartHookOutput",
    "UserPromptSubmitHookInput",
    "UserPromptSubmitHookOutput",
    "UserPromptSubmitResult",
]
