from __future__ import annotations

import logging

from vibe.core.hooks._handler import (
    HookExternalAttrs,
    HookHandler,
    HookRetryState,
    _append_text,
    _HookAction,
)
from vibe.core.hooks.config import HookConfig
from vibe.core.hooks.models import (
    HookEndEvent,
    HookInvocation,
    HookMessageSeverity,
    HookStructuredResponse,
    HookTextReplacement,
    PostToolInvocation,
)
from vibe.core.utils.matching import name_matches

logger = logging.getLogger(__name__)


def _as_post_tool(invocation: HookInvocation) -> PostToolInvocation:
    if not isinstance(invocation, PostToolInvocation):
        raise TypeError(
            f"PostToolHandler expected PostToolInvocation, got"
            f" {type(invocation).__name__}"
        )
    return invocation


class PostToolHandler(HookHandler):
    """Deny → replace ``tool_output_text`` with ``reason`` (then append
    ``additional_context`` if present). Plain ``additional_context`` →
    append to ``tool_output_text``.
    """

    def matches(self, hook: HookConfig, invocation: HookInvocation) -> bool:
        return name_matches(_as_post_tool(invocation).tool_name, [hook.match or "*"])

    def external_attributes(self, invocation: HookInvocation) -> HookExternalAttrs:
        inv = _as_post_tool(invocation)
        return {"tool_name": inv.tool_name, "tool_call_id": inv.tool_call_id}

    def _on_deny(
        self,
        hook: HookConfig,
        invocation: HookInvocation,
        response: HookStructuredResponse,
        retry_state: HookRetryState,
    ) -> _HookAction:
        inv = _as_post_tool(invocation)
        reason = response.reason or ""
        additional = response.hook_specific_output.additional_context
        final_text = (
            _append_text(reason, additional) if additional is not None else reason
        )
        return _HookAction(
            events=[
                HookEndEvent(
                    hook_name=hook.name,
                    status=HookMessageSeverity.WARNING,
                    content=response.system_message
                    or f"Replaced tool result ({len(final_text)} chars)",
                ),
                HookTextReplacement(text=final_text),
            ],
            next_invocation=inv.model_copy(update={"tool_output_text": final_text}),
            should_break=False,
        )

    def _on_allow(
        self,
        hook: HookConfig,
        invocation: HookInvocation,
        response: HookStructuredResponse,
        retry_state: HookRetryState,
    ) -> _HookAction:
        if response.hook_specific_output.tool_input is not None:
            logger.warning(
                "Hook %s: 'hook_specific_output.tool_input' is only"
                " meaningful for pre_tool; ignoring",
                hook.name,
            )
        additional = response.hook_specific_output.additional_context
        if additional is None:
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
        inv = _as_post_tool(invocation)
        new_text = _append_text(inv.tool_output_text, additional)
        return _HookAction(
            events=[
                HookEndEvent(
                    hook_name=hook.name,
                    status=HookMessageSeverity.WARNING,
                    content=response.system_message
                    or f"Appended {len(additional)} chars to tool result",
                ),
                HookTextReplacement(text=new_text),
            ],
            next_invocation=inv.model_copy(update={"tool_output_text": new_text}),
            should_break=False,
        )

    def on_passthrough(self, hook: HookConfig, retry_state: HookRetryState) -> None:
        return

    def on_strict_failure(
        self, hook: HookConfig, invocation: HookInvocation, reason: str
    ) -> _HookAction | None:
        inv = _as_post_tool(invocation)
        return _HookAction(
            events=[
                HookEndEvent(
                    hook_name=hook.name,
                    status=HookMessageSeverity.ERROR,
                    content="Cleared tool result (strict)",
                ),
                HookTextReplacement(text=""),
            ],
            next_invocation=inv.model_copy(update={"tool_output_text": ""}),
            should_break=True,
        )
