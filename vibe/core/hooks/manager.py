"""Hook manager module.

Provides a high-level API for managing and executing hooks throughout
the agent's lifecycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import re
from typing import Any

from pydantic_core import to_jsonable_python

from vibe.core.hooks.executor import execute_hooks_parallel
from vibe.core.hooks.types import (
    HookConfig,
    HookEvent,
    HookPermissionDecision,
    HookResult,
    HooksConfig,
    PostToolUseHookInput,
    PreToolUseHookInput,
    PreToolUseHookOutput,
    SessionEndHookInput,
    SessionStartHookInput,
    UserPromptSubmitHookInput,
    UserPromptSubmitHookOutput,
)

logger = logging.getLogger(__name__)


@dataclass
class PreToolUseResult:
    """Result of running PreToolUse hooks.

    Attributes:
        should_execute: Whether the tool should be executed.
        decision: The permission decision (allow/deny/ask).
        updated_input: Modified tool input arguments, if any.
        reason: Reason for blocking, if applicable.
        system_messages: Messages to inject into the conversation.
    """

    should_execute: bool = True
    decision: HookPermissionDecision = HookPermissionDecision.ALLOW
    updated_input: dict[str, Any] | None = None
    reason: str | None = None
    system_messages: list[str] = field(default_factory=list)


@dataclass
class PostToolUseResult:
    """Result of running PostToolUse hooks.

    Attributes:
        system_messages: Messages to inject into the conversation.
    """

    system_messages: list[str] = field(default_factory=list)


@dataclass
class UserPromptSubmitResult:
    """Result of running UserPromptSubmit hooks.

    Attributes:
        should_continue: Whether to continue with the prompt.
        modified_prompt: Modified prompt, if any.
        block_reason: Reason for blocking, if applicable.
        system_messages: Messages to inject into the conversation.
    """

    should_continue: bool = True
    modified_prompt: str | None = None
    block_reason: str | None = None
    system_messages: list[str] = field(default_factory=list)


class HookManager:
    """Manages hook execution throughout the agent lifecycle.

    This class provides a clean interface for executing hooks at various
    points in the agent's execution, handling matching, execution, and
    result aggregation.
    """

    def __init__(
        self,
        config: HooksConfig | None = None,
        config_getter: Callable[[], HooksConfig] | None = None,
        session_id: str = "",
        cwd: str = "",
    ) -> None:
        """Initialize the hook manager.

        Args:
            config: Static hooks configuration (for testing).
            config_getter: Callable that returns current hooks configuration.
            session_id: Current session ID.
            cwd: Current working directory.
        """
        self._config = config
        self._config_getter = config_getter
        self.session_id = session_id
        self.cwd = cwd
        self._matcher_cache: dict[str, re.Pattern[str]] = {}

    @property
    def config(self) -> HooksConfig:
        """Get the current hooks configuration."""
        if self._config_getter:
            return self._config_getter()
        return self._config or HooksConfig()

    @property
    def enabled(self) -> bool:
        """Check if hooks are enabled."""
        return self.config.enabled

    def _get_matcher_pattern(self, matcher: str) -> re.Pattern[str]:
        """Compile and cache a matcher pattern."""
        if matcher not in self._matcher_cache:
            if matcher == "*":
                pattern = re.compile(r".*")
            else:
                try:
                    pattern = re.compile(matcher, re.IGNORECASE)
                except re.error:
                    logger.warning(f"Invalid matcher pattern: {matcher}")
                    pattern = re.compile(re.escape(matcher), re.IGNORECASE)
            self._matcher_cache[matcher] = pattern
        return self._matcher_cache[matcher]

    def _filter_hooks_by_tool(
        self, hooks: list[HookConfig], tool_name: str
    ) -> list[HookConfig]:
        """Filter hooks that match the given tool name."""
        matching = []
        for hook in hooks:
            pattern = self._get_matcher_pattern(hook.matcher)
            if pattern.match(tool_name):
                matching.append(hook)
        return matching

    def _jsonable_tool_input(self, tool_input: dict[str, Any]) -> dict[str, Any]:
        jsonable = to_jsonable_python(tool_input, fallback=str)
        if isinstance(jsonable, dict):
            return jsonable
        logger.warning(
            "Hook tool_input serialization produced non-dict output; wrapping value"
        )
        return {"value": jsonable}

    async def run_pre_tool_use(
        self, tool_name: str, tool_input: dict[str, Any]
    ) -> PreToolUseResult:
        """Run PreToolUse hooks before a tool is executed.

        Args:
            tool_name: Name of the tool about to be executed.
            tool_input: Input arguments for the tool.

        Returns:
            PreToolUseResult with decision and any modifications.
        """
        if not self.enabled:
            return PreToolUseResult()

        hooks = self._filter_hooks_by_tool(
            self.config.hooks.PreToolUse, tool_name
        )
        if not hooks:
            return PreToolUseResult()

        hook_input = PreToolUseHookInput(
            session_id=self.session_id,
            cwd=self.cwd,
            hook_event_name=HookEvent.PRE_TOOL_USE.value,
            tool_name=tool_name,
            tool_input=self._jsonable_tool_input(tool_input),
        )

        results = await execute_hooks_parallel(hooks, hook_input, self.cwd)
        return self._aggregate_pre_tool_results(results)

    def _aggregate_pre_tool_results(
        self, results: list[HookResult]
    ) -> PreToolUseResult:
        """Aggregate results from multiple PreToolUse hooks.

        Blocking takes priority over allowing. If any hook denies,
        the tool execution is blocked.
        """
        aggregated = PreToolUseResult()
        system_messages: list[str] = []

        for result in results:
            output = result.output
            if result.error:
                logger.warning(f"Hook error: {result.error}")
            if not output.continue_:
                aggregated.should_execute = False
                aggregated.decision = HookPermissionDecision.DENY
                if aggregated.reason is None:
                    if isinstance(output, PreToolUseHookOutput) and output.reason:
                        aggregated.reason = output.reason
                    elif result.error:
                        aggregated.reason = result.error
                    else:
                        aggregated.reason = "Blocked by hook"

            if isinstance(output, PreToolUseHookOutput):
                if output.permission_decision == HookPermissionDecision.DENY:
                    aggregated.should_execute = False
                    aggregated.decision = HookPermissionDecision.DENY
                    if output.reason:
                        aggregated.reason = output.reason
                    elif aggregated.reason is None:
                        aggregated.reason = "Blocked by hook"
                elif (
                    output.permission_decision == HookPermissionDecision.ASK
                    and aggregated.decision != HookPermissionDecision.DENY
                ):
                    aggregated.decision = HookPermissionDecision.ASK

                if output.updated_input is not None:
                    if aggregated.updated_input is None:
                        aggregated.updated_input = {}
                    aggregated.updated_input.update(output.updated_input)

            if output.system_message:
                system_messages.append(output.system_message)

        aggregated.system_messages = system_messages
        return aggregated

    async def run_post_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_result: str | None = None,
        tool_error: str | None = None,
    ) -> PostToolUseResult:
        """Run PostToolUse hooks after a tool is executed.

        Args:
            tool_name: Name of the tool that was executed.
            tool_input: Input arguments for the tool.
            tool_result: Result from the tool, if successful.
            tool_error: Error from the tool, if failed.

        Returns:
            PostToolUseResult with any system messages.
        """
        if not self.enabled:
            return PostToolUseResult()

        hooks = self._filter_hooks_by_tool(
            self.config.hooks.PostToolUse, tool_name
        )
        if not hooks:
            return PostToolUseResult()

        hook_input = PostToolUseHookInput(
            session_id=self.session_id,
            cwd=self.cwd,
            hook_event_name=HookEvent.POST_TOOL_USE.value,
            tool_name=tool_name,
            tool_input=self._jsonable_tool_input(tool_input),
            tool_result=tool_result,
            tool_error=tool_error,
        )

        results = await execute_hooks_parallel(hooks, hook_input, self.cwd)
        return self._aggregate_post_tool_results(results)

    def _aggregate_post_tool_results(
        self, results: list[HookResult]
    ) -> PostToolUseResult:
        """Aggregate results from multiple PostToolUse hooks."""
        system_messages: list[str] = []

        for result in results:
            output = result.output
            if result.error:
                logger.warning(f"Hook error: {result.error}")

            if output.system_message:
                system_messages.append(output.system_message)

        return PostToolUseResult(system_messages=system_messages)

    async def run_session_start(self) -> list[str]:
        """Run SessionStart hooks when a session begins.

        Returns:
            List of system messages to inject.
        """
        if not self.enabled:
            return []

        hooks = self.config.hooks.SessionStart
        if not hooks:
            return []

        hook_input = SessionStartHookInput(
            session_id=self.session_id,
            cwd=self.cwd,
            hook_event_name=HookEvent.SESSION_START.value,
        )

        results = await execute_hooks_parallel(hooks, hook_input, self.cwd)
        return [
            r.output.system_message for r in results if r.output.system_message
        ]

    async def run_session_end(
        self, message_count: int, total_tokens: int
    ) -> None:
        """Run SessionEnd hooks when a session ends.

        Args:
            message_count: Number of messages in the session.
            total_tokens: Total tokens used in the session.
        """
        if not self.enabled:
            return

        hooks = self.config.hooks.SessionEnd
        if not hooks:
            return

        hook_input = SessionEndHookInput(
            session_id=self.session_id,
            cwd=self.cwd,
            hook_event_name=HookEvent.SESSION_END.value,
            message_count=message_count,
            total_tokens=total_tokens,
        )

        await execute_hooks_parallel(hooks, hook_input, self.cwd)

    async def run_user_prompt_submit(
        self, user_prompt: str
    ) -> UserPromptSubmitResult:
        """Run UserPromptSubmit hooks when a user submits a prompt.

        Args:
            user_prompt: The user's prompt text.

        Returns:
            UserPromptSubmitResult with decision and any modifications.
        """
        if not self.enabled:
            return UserPromptSubmitResult()

        hooks = self.config.hooks.UserPromptSubmit
        if not hooks:
            return UserPromptSubmitResult()

        hook_input = UserPromptSubmitHookInput(
            session_id=self.session_id,
            cwd=self.cwd,
            hook_event_name=HookEvent.USER_PROMPT_SUBMIT.value,
            user_prompt=user_prompt,
        )

        results = await execute_hooks_parallel(hooks, hook_input, self.cwd)
        return self._aggregate_user_prompt_results(results, user_prompt)

    def _aggregate_user_prompt_results(
        self, results: list[HookResult], original_prompt: str
    ) -> UserPromptSubmitResult:
        """Aggregate results from multiple UserPromptSubmit hooks."""
        aggregated = UserPromptSubmitResult()
        system_messages: list[str] = []
        modified_prompt = original_prompt

        for result in results:
            output = result.output
            if result.error:
                logger.warning(f"Hook error: {result.error}")

            if not output.continue_:
                aggregated.should_continue = False
                if aggregated.block_reason is None:
                    if output.system_message:
                        aggregated.block_reason = output.system_message
                    elif result.error:
                        aggregated.block_reason = result.error

            if isinstance(output, UserPromptSubmitHookOutput):
                if output.modified_prompt:
                    modified_prompt = output.modified_prompt

            if output.system_message:
                system_messages.append(output.system_message)

        if modified_prompt != original_prompt:
            aggregated.modified_prompt = modified_prompt

        aggregated.system_messages = system_messages
        return aggregated

    def update_session_info(self, session_id: str, cwd: str) -> None:
        """Update session information.

        Args:
            session_id: New session ID.
            cwd: New working directory.
        """
        self.session_id = session_id
        self.cwd = cwd
