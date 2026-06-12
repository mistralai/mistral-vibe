from __future__ import annotations

import logging

from vibe.core.hooks._handler import (
    HookExternalAttrs,
    HookHandler,
    HookRetryState,
    _HookAction,
)
from vibe.core.hooks.config import HookConfig
from vibe.core.hooks.models import (
    BeforeToolInvocation,
    HookEndEvent,
    HookInvocation,
    HookMessageSeverity,
    HookStructuredResponse,
    HookToolDenial,
    HookToolInputRewrite,
)
from vibe.core.utils.matching import name_matches

logger = logging.getLogger(__name__)


def _as_before(invocation: HookInvocation) -> BeforeToolInvocation:
    if not isinstance(invocation, BeforeToolInvocation):
        raise TypeError(
            f"BeforeToolHandler expected BeforeToolInvocation, got"
            f" {type(invocation).__name__}"
        )
    return invocation


class BeforeToolHandler(HookHandler):
    """Deny → ``HookToolDenial``; ``tool_input`` rewrite → one
    ``HookToolInputRewrite`` per rewriting hook (validated by the agent
    loop, first invalid rewrite aborts the chain).
    """

    def matches(self, hook: HookConfig, invocation: HookInvocation) -> bool:
        return name_matches(_as_before(invocation).tool_name, [hook.match or "*"])

    def external_attributes(self, invocation: HookInvocation) -> HookExternalAttrs:
        inv = _as_before(invocation)
        return {"tool_name": inv.tool_name, "tool_call_id": inv.tool_call_id}

    def _on_deny(
        self,
        hook: HookConfig,
        invocation: HookInvocation,
        response: HookStructuredResponse,
        retry_state: HookRetryState,
    ) -> _HookAction:
        inv = _as_before(invocation)
        return _HookAction(
            events=[
                HookEndEvent(
                    hook_name=hook.name,
                    status=HookMessageSeverity.ERROR,
                    content=f"Denied tool '{inv.tool_name}'",
                ),
                HookToolDenial(hook_name=hook.name, content=response.reason or ""),
            ],
            next_invocation=None,
            should_break=True,
        )

    def _on_allow(
        self,
        hook: HookConfig,
        invocation: HookInvocation,
        response: HookStructuredResponse,
        retry_state: HookRetryState,
    ) -> _HookAction:
        if response.hook_specific_output.additional_context is not None:
            logger.warning(
                "Hook %s: 'hook_specific_output.additional_context' is only"
                " meaningful for after_tool; ignoring",
                hook.name,
            )
        rewrite = response.hook_specific_output.tool_input
        if rewrite is None:
            return _HookAction(
                events=[
                    HookEndEvent(
                        hook_name=hook.name,
                        status=HookMessageSeverity.OK,
                        content=response.system_message,
                    )
                ],
                next_invocation=None,
                should_break=False,
            )
        inv = _as_before(invocation)
        return _HookAction(
            events=[
                HookEndEvent(
                    hook_name=hook.name,
                    status=HookMessageSeverity.WARNING,
                    content=response.system_message
                    or f"Rewrote tool_input for '{inv.tool_name}'",
                ),
                HookToolInputRewrite(hook_name=hook.name, tool_input=rewrite),
            ],
            next_invocation=inv.model_copy(update={"tool_input": rewrite}),
            should_break=False,
        )

    def on_passthrough(self, hook: HookConfig, retry_state: HookRetryState) -> None:
        return

    def on_strict_failure(
        self, hook: HookConfig, invocation: HookInvocation, reason: str
    ) -> _HookAction | None:
        inv = _as_before(invocation)
        return _HookAction(
            events=[
                HookEndEvent(
                    hook_name=hook.name,
                    status=HookMessageSeverity.ERROR,
                    content=f"Denied tool '{inv.tool_name}' (strict)",
                ),
                HookToolDenial(hook_name=hook.name, content=reason),
            ],
            next_invocation=None,
            should_break=True,
        )
