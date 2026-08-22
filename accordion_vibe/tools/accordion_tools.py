"""The two agent-facing Accordion tools, relayed to the sidecar.

Both mirror the pi tools ``extension/accordion.ts`` registers: ``unfold``
reopens folded blocks by their ``{#code FOLDED}`` codes, ``recall`` reads one
folded block's content without changing what the model sees. Neither does any
work here — they forward to the sidecar, which resolves against the
authoritative Truth.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from accordion_vibe._bridge import bridge_for_session
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)

if TYPE_CHECKING:
    from accordion_vibe._bridge import AccordionBridge

__all__ = ["Recall", "Unfold"]


class AccordionToolConfig(BaseToolConfig):
    """Accordion's own tools never need approval: they only change the view."""

    permission: ToolPermission = ToolPermission.ALWAYS


class UnfoldArgs(BaseModel):
    codes: list[str] = Field(
        description=(
            "The short codes from the '{#code FOLDED}' tags of the blocks to "
            "reopen, e.g. ['a1f', '9c2']."
        )
    )


class RecallArgs(BaseModel):
    codes: list[str] = Field(
        description=(
            "The short codes from '{#code FOLDED}' tags whose full content "
            "should be read back without unfolding them."
        )
    )


class AccordionToolResult(BaseModel):
    content: str


def _bridge(ctx: InvokeContext | None) -> AccordionBridge:
    bridge = bridge_for_session(ctx.session_id if ctx is not None else None)
    if bridge is None or not bridge.attached:
        raise ToolError("Accordion is not attached to this session.")
    return bridge


async def _relay(
    ctx: InvokeContext | None, name: str, args: dict[str, Any]
) -> AccordionToolResult:
    bridge = _bridge(ctx)
    call_id = ctx.tool_call_id if ctx is not None else None
    content, is_error = await asyncio.to_thread(bridge.call_tool, name, args, call_id)
    if is_error:
        raise ToolError(content or f"Accordion tool {name!r} failed.")
    return AccordionToolResult(content=content)


class Unfold(
    BaseTool[UnfoldArgs, AccordionToolResult, AccordionToolConfig, BaseToolState]
):
    description = (
        "Reopen folded context blocks by their {#code FOLDED} codes so their "
        "full content is visible again on every later model call."
    )

    async def run(
        self, args: UnfoldArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[AccordionToolResult, None]:
        yield await _relay(ctx, "unfold", {"codes": list(args.codes)})


class Recall(
    BaseTool[RecallArgs, AccordionToolResult, AccordionToolConfig, BaseToolState]
):
    description = (
        "Read the full content of one folded block by its {#code FOLDED} code "
        "without unfolding it; the context the model sees is unchanged."
    )

    async def run(
        self, args: RecallArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[AccordionToolResult, None]:
        yield await _relay(ctx, "recall", {"codes": list(args.codes)})
