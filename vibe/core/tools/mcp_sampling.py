from __future__ import annotations

from collections.abc import Callable
from logging import getLogger
from typing import Any

from mcp.client.session import ClientSession
from mcp.shared.context import RequestContext
from mcp.types import (
    CreateMessageRequestParams,
    CreateMessageResult,
    ErrorData,
    TextContent,
)

from vibe.core.llm.types import BackendLike
from vibe.core.types import LLMMessage, Role

logger = getLogger("vibe")


class MCPSamplingHandler:
    def __init__(
        self,
        backend_getter: Callable[[], BackendLike],
        config_getter: Callable[[], Any],
    ) -> None:
        self._backend_getter = backend_getter
        self._config_getter = config_getter

    async def __call__(
        self,
        context: RequestContext[ClientSession, Any],
        params: CreateMessageRequestParams,
    ) -> CreateMessageResult | ErrorData:
        try:
            config = self._config_getter()
            model = config.get_active_model()

            messages = _map_sampling_messages(params.messages)

            if params.systemPrompt:
                messages.insert(
                    0, LLMMessage(role=Role.system, content=params.systemPrompt)
                )

            result = await self._backend_getter().complete(
                model=model,
                messages=messages,
                temperature=params.temperature
                if params.temperature is not None
                else model.temperature,
                tools=None,
                max_tokens=params.maxTokens,
                tool_choice=None,
                extra_headers=None,
            )

            content_text = result.message.content or ""

            return CreateMessageResult(
                role="assistant",
                content=TextContent(type="text", text=content_text),
                model=model.name,
                stopReason="endTurn",
            )

        except Exception as exc:
            logger.warning("MCP sampling request failed: %s", exc)
            return ErrorData(code=-1, message=f"Sampling failed: {exc}")


def _map_sampling_messages(messages: list[Any]) -> list[LLMMessage]:
    result: list[LLMMessage] = []
    for msg in messages:
        match msg.role:
            case "user":
                role = Role.user
            case "assistant":
                role = Role.assistant
            case _:
                logger.error(
                    "MCP sampling: unexpected message role, treating as assistant",
                    extra={"role": getattr(msg, "role", None)},
                )
                role = Role.assistant
        content = _extract_text_content(msg.content)
        result.append(LLMMessage(role=role, content=content))
    return result


def _extract_text_content(content: Any) -> str:
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if (
                hasattr(block, "type")
                and block.type == "text"
                and hasattr(block, "text")
            ):
                parts.append(block.text)
            else:
                block_type = getattr(block, "type", "unknown")
                logger.debug(
                    "MCP sampling: skipping unsupported content block type=%s",
                    block_type,
                )
        return "\n".join(parts) if parts else ""

    if hasattr(content, "type") and content.type == "text" and hasattr(content, "text"):
        return content.text

    block_type = getattr(content, "type", "unknown")
    logger.debug("MCP sampling: unsupported single content block type=%s", block_type)
    return ""
