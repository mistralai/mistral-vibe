from __future__ import annotations

from collections.abc import AsyncGenerator
import logging

from vibe.core.hooks._handler import (
    HookExternalAttrs,
    HookHandler,
    HookOutputError,
    HookRetryState,
    _failure_reason,
    _HookAction,
    _HookYield,
    _parse_structured_response,
)
from vibe.core.hooks._post_agent import PostAgentHandler
from vibe.core.hooks._post_tool import PostToolHandler
from vibe.core.hooks._pre_tool import PreToolHandler
from vibe.core.hooks.config import HookConfig
from vibe.core.hooks.executor import HookExecutor
from vibe.core.hooks.models import (
    HookEndEvent,
    HookExecutionResult,
    HookInvocation,
    HookMessageSeverity,
    HookRunEndEvent,
    HookRunStartEvent,
    HookStartEvent,
    HookType,
)
from vibe.core.tracing import hook_span

logger = logging.getLogger(__name__)


_HANDLERS: dict[HookType, HookHandler] = {
    HookType.POST_AGENT: PostAgentHandler(),
    HookType.PRE_TOOL: PreToolHandler(),
    HookType.POST_TOOL: PostToolHandler(),
}


class HooksManager:
    """Orchestrates hook subprocesses and dispatches their results to the
    per-type :class:`HookHandler`. The manager treats invocations as
    opaque values and threads them across the chain via
    ``action.next_invocation``.
    """

    def __init__(self, hooks: list[HookConfig]) -> None:
        self._hooks_by_type: dict[HookType, list[HookConfig]] = {}
        for hook in hooks:
            self._hooks_by_type.setdefault(hook.type, []).append(hook)
        self._executor = HookExecutor()
        self._retry_state = HookRetryState()

    def has_hooks(self, hook_type: HookType) -> bool:
        return bool(self._hooks_by_type.get(hook_type))

    def reset_retry_count(self) -> None:
        self._retry_state.reset()

    def _matching_hooks(
        self, handler: HookHandler, invocation: HookInvocation
    ) -> list[HookConfig]:
        hook_type = HookType(invocation.hook_event_name)
        return [
            h
            for h in self._hooks_by_type.get(hook_type, [])
            if handler.matches(h, invocation)
        ]

    async def _run_subprocess(
        self,
        hook: HookConfig,
        invocation: HookInvocation,
        external_attrs: HookExternalAttrs,
    ) -> HookExecutionResult:
        async with hook_span(
            hook_name=hook.name,
            hook_type=hook.type.value,
            tool_name=external_attrs.get("tool_name"),
            tool_call_id=external_attrs.get("tool_call_id"),
        ):
            return await self._executor.run(hook, invocation)

    def _process_hook_result(
        self,
        handler: HookHandler,
        hook: HookConfig,
        invocation: HookInvocation,
        result: HookExecutionResult,
    ) -> _HookAction:
        # Non-zero exit / timeout / non-conforming stdout all route through
        # _handle_failure; empty stdout is a passthrough; valid JSON goes
        # to the handler's on_structured.
        if result.timed_out or result.exit_code != 0:
            return self._handle_failure(
                handler,
                hook,
                invocation,
                reason=_failure_reason(result),
                warn_content=(
                    f"Timed out after {hook.timeout}s"
                    if result.timed_out or result.exit_code is None
                    else None
                ),
            )

        try:
            structured = _parse_structured_response(result.stdout)
        except HookOutputError as e:
            return self._handle_failure(
                handler, hook, invocation, reason=f"invalid response: {e}"
            )

        if structured is not None:
            return handler.on_structured(
                hook, invocation, structured, self._retry_state
            )

        handler.on_passthrough(hook, self._retry_state)
        return _HookAction(
            events=[HookEndEvent(hook_name=hook.name, status=HookMessageSeverity.OK)],
            next_invocation=None,
            should_break=False,
        )

    def _handle_failure(
        self,
        handler: HookHandler,
        hook: HookConfig,
        invocation: HookInvocation,
        *,
        reason: str,
        warn_content: str | None = None,
    ) -> _HookAction:
        if hook.strict:
            escalation = handler.on_strict_failure(hook, invocation, reason)
            if escalation is not None:
                return escalation

        handler.on_passthrough(hook, self._retry_state)
        return _HookAction(
            events=[
                HookEndEvent(
                    hook_name=hook.name,
                    status=HookMessageSeverity.WARNING,
                    content=warn_content or reason,
                )
            ],
            next_invocation=None,
            should_break=False,
        )

    async def run(self, invocation: HookInvocation) -> AsyncGenerator[_HookYield]:
        """Run all hooks matching *invocation* and stream their events."""
        hook_type = HookType(invocation.hook_event_name)
        handler = _HANDLERS[hook_type]
        hooks = self._matching_hooks(handler, invocation)
        if not hooks:
            return

        external_attrs = handler.external_attributes(invocation)
        current = invocation

        yield HookRunStartEvent(
            scope=hook_type,
            tool_name=external_attrs.get("tool_name"),
            tool_call_id=external_attrs.get("tool_call_id"),
        )

        tool_call_id = external_attrs.get("tool_call_id")
        for hook in hooks:
            yield HookStartEvent(
                hook_name=hook.name, scope=hook_type, tool_call_id=tool_call_id
            )
            result = await self._run_subprocess(hook, current, external_attrs)

            action = self._process_hook_result(handler, hook, current, result)
            for ev in action.events:
                if isinstance(ev, HookEndEvent):
                    yield ev.model_copy(
                        update={"scope": hook_type, "tool_call_id": tool_call_id}
                    )
                else:
                    yield ev
            if action.next_invocation is not None:
                current = action.next_invocation
            if action.should_break:
                break

        yield HookRunEndEvent(scope=hook_type, tool_call_id=tool_call_id)
