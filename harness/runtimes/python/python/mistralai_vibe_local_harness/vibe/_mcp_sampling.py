"""MCP SDK boundary for server-initiated sampling requests."""

import asyncio
import logging
from typing import Any, cast

from mcp.client.session import ClientSession
from mcp.shared.context import RequestContext
from mcp.types import (
    CreateMessageRequestParams,
    CreateMessageResult,
    ErrorData,
    TextContent,
)

from mistralai_vibe_local_harness.vibe._mcp_models import (
    MCPSamplingCallback,
    MCPSamplingCompletion,
    MCPSamplingMessage,
    MCPSamplingRequest,
)

logger = logging.getLogger(__name__)


def build_sampling_callback(
    completion: MCPSamplingCompletion,
) -> MCPSamplingCallback:
    async def sample(
        context: RequestContext[ClientSession, Any],
        params: CreateMessageRequestParams,
    ) -> CreateMessageResult | ErrorData:
        del context
        try:
            request = MCPSamplingRequest(
                messages=tuple(
                    MCPSamplingMessage(role=message.role, text=_content_text(message.content))
                    for message in params.messages
                ),
                system_prompt=params.systemPrompt,
                temperature=params.temperature,
                max_tokens=params.maxTokens,
            )
            response = await completion(request)
            return CreateMessageResult(
                role="assistant",
                content=TextContent(type="text", text=response.text),
                model=response.model,
                stopReason="endTurn",
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("MCP sampling request failed", exc_info=exc)
            return ErrorData(code=-1, message="Sampling failed")

    return cast(MCPSamplingCallback, sample)


def _content_text(content: object) -> str:
    blocks = content if isinstance(content, list) else [content]
    return "\n".join(
        str(getattr(block, "text"))
        for block in blocks
        if getattr(block, "type", None) == "text" and isinstance(getattr(block, "text", None), str)
    )


__all__ = ["build_sampling_callback"]
