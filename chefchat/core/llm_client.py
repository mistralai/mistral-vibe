from __future__ import annotations

from collections import OrderedDict
from collections.abc import AsyncGenerator
import time
from typing import TYPE_CHECKING

from chefchat.core.agent import LLMResponseError
from chefchat.core.config import DEFAULT_MAX_TOKENS, VibeConfig
from chefchat.core.llm.backend.factory import BACKEND_FACTORY
from chefchat.core.llm.format import APIToolFormatHandler
from chefchat.core.llm.types import BackendLike
from chefchat.core.middleware import MiddlewarePipeline
from chefchat.core.types import (
    AgentStats,
    AssistantEvent,
    LLMChunk,
    LLMMessage,
    Role,
    ToolCall,
)
from chefchat.core.utils import get_user_agent

if TYPE_CHECKING:
    from chefchat.core.tools.manager import ToolManager


class LLMClient:
    """Handles direct interactions with the LLM backend."""

    def __init__(
        self,
        config: VibeConfig,
        stats: AgentStats,
        session_id: str,
        tool_manager: ToolManager,
        format_handler: APIToolFormatHandler,
        middleware_pipeline: MiddlewarePipeline,
        backend: BackendLike | None = None,
    ) -> None:
        self.config = config
        self.stats = stats
        self.session_id = session_id
        self.tool_manager = tool_manager
        self.format_handler = format_handler
        self.middleware_pipeline = middleware_pipeline

        self.backend_factory = lambda: backend or self._select_backend()
        self.backend = self.backend_factory()

        # Last chunk stored for internal state tracking if needed by Agent (Agent seemingly accesses it)
        # But Agent accesses it via `self._last_chunk`. We should probably expose it or return it.
        self.last_chunk: LLMChunk | None = None

    def _select_backend(self) -> BackendLike:
        active_model = self.config.get_active_model()
        provider = self.config.get_provider_for_model(active_model)
        backend_cls = BACKEND_FACTORY[provider.backend]
        return backend_cls(provider=provider, timeout=self.config.api_timeout)

    def reload(self, config: VibeConfig) -> None:
        """Reload client with new configuration."""
        self.config = config
        self.backend = self.backend_factory()

    def create_assistant_event(
        self, content: str, chunk: LLMChunk | None
    ) -> AssistantEvent:
        return AssistantEvent(
            content=content,
            prompt_tokens=chunk.usage.prompt_tokens if chunk and chunk.usage else 0,
            completion_tokens=chunk.usage.completion_tokens
            if chunk and chunk.usage
            else 0,
            session_total_tokens=self.stats.session_total_llm_tokens,
            last_turn_duration=self.stats.last_turn_duration,
            tokens_per_second=self.stats.tokens_per_second,
        )

    async def chat(
        self, messages: list[LLMMessage], max_tokens: int | None = None
    ) -> LLMChunk:
        active_model = self.config.get_active_model()
        provider = self.config.get_provider_for_model(active_model)

        try:
            start_time = time.perf_counter()
            available_tools = self.format_handler.get_available_tools(
                self.tool_manager, self.config
            )
            tool_choice = self.format_handler.get_tool_choice()

            async with self.backend as backend:
                result = await backend.complete(
                    model=active_model,
                    messages=messages,
                    temperature=active_model.temperature,
                    tools=available_tools,
                    tool_choice=tool_choice,
                    extra_headers={
                        "User-Agent": get_user_agent(),
                        "x-affinity": self.session_id,
                    },
                    max_tokens=max_tokens or active_model.max_tokens or DEFAULT_MAX_TOKENS,
                )

            end_time = time.perf_counter()

            self.stats.last_turn_duration = end_time - start_time
            self.stats.last_turn_prompt_tokens = result.usage.prompt_tokens
            self.stats.last_turn_completion_tokens = result.usage.completion_tokens
            self.stats.session_prompt_tokens += result.usage.prompt_tokens
            self.stats.session_completion_tokens += result.usage.completion_tokens
            self.stats.context_tokens = (
                result.usage.prompt_tokens + result.usage.completion_tokens
            )

            self.last_chunk = result
            return result

        except Exception as e:
            # Check if this is a BackendError with context-too-long
            from chefchat.core.llm.exceptions import BackendError

            if isinstance(e, BackendError) and e.is_context_too_long():
                # Convert to user-friendly error with recovery hints
                error_msg = f"""**Prompt Too Long Error**

The system prompt exceeded the model's token limit.

**Details:**
- Model: {e.model}
- Approximate size: {e.payload_summary.approx_chars:,} characters
- Provider message: {e.parsed_error or 'N/A'}

**Recovery Options:**
1. **Switch to YOLO mode** (🚀) - Uses minimal prompts
2. **Clear conversation** with `/clear`
3. **Reduce project context** in configuration
4. **Use a model with larger context window**

Press `Shift+Tab` to cycle modes or type `/modes` for options.
"""
                raise RuntimeError(error_msg) from e

            # For other errors, use the original error message
            raise RuntimeError(
                f"API error from {provider.name} (model: {active_model.name}): {e}"
            ) from e

    async def _chat_streaming(
        self, messages: list[LLMMessage], max_tokens: int | None = None
    ) -> AsyncGenerator[LLMChunk]:
        active_model = self.config.get_active_model()
        provider = self.config.get_provider_for_model(active_model)

        available_tools = self.format_handler.get_available_tools(
            self.tool_manager, self.config
        )
        tool_choice = self.format_handler.get_tool_choice()
        try:
            start_time = time.perf_counter()
            last_chunk = None
            async with self.backend as backend:
                async for chunk in backend.complete_streaming(
                    model=active_model,
                    messages=messages,
                    temperature=active_model.temperature,
                    tools=available_tools,
                    tool_choice=tool_choice,
                    extra_headers={
                        "User-Agent": get_user_agent(),
                        "x-affinity": self.session_id,
                    },
                    max_tokens=max_tokens or active_model.max_tokens or DEFAULT_MAX_TOKENS,
                ):
                    yield chunk
                    last_chunk = chunk

            end_time = time.perf_counter()
            self.stats.last_turn_duration = end_time - start_time
            if last_chunk is None or last_chunk.usage is None:
                # Should probably warn, but for now rely on backend
                pass
            else:
                self.stats.last_turn_prompt_tokens = last_chunk.usage.prompt_tokens
                self.stats.last_turn_completion_tokens = (
                    last_chunk.usage.completion_tokens
                )
                self.stats.session_prompt_tokens += last_chunk.usage.prompt_tokens
                self.stats.session_completion_tokens += (
                    last_chunk.usage.completion_tokens
                )
                self.stats.context_tokens = (
                    last_chunk.usage.prompt_tokens + last_chunk.usage.completion_tokens
                )

        except Exception as e:
            # Check if this is a BackendError with context-too-long
            from chefchat.core.llm.exceptions import BackendError

            if isinstance(e, BackendError) and e.is_context_too_long():
                # Convert to user-friendly error with recovery hints
                error_msg = f"""**Prompt Too Long Error**

The system prompt exceeded the model's token limit.

**Details:**
- Model: {e.model}
- Approximate size: {e.payload_summary.approx_chars:,} characters
- Provider message: {e.parsed_error or 'N/A'}

**Recovery Options:**
1. **Switch to YOLO mode** (🚀) - Uses minimal prompts
2. **Clear conversation** with `/clear`
3. **Reduce project context** in configuration
4. **Use a model with larger context window**

Press `Shift+Tab` to cycle modes or type `/modes` for options.
"""
                raise RuntimeError(error_msg) from e

            # For other errors, use the original error message
            raise RuntimeError(
                f"API error from {provider.name} (model: {active_model.name}): {e}"
            ) from e

    async def stream_assistant_events(
        self, messages: list[LLMMessage]
    ) -> AsyncGenerator[AssistantEvent]:
        chunks: list[LLMChunk] = []
        content_buffer = ""
        chunks_with_content = 0
        BATCH_SIZE = 5

        async for chunk in self._chat_streaming(messages):
            chunks.append(chunk)

            if chunk.message.tool_calls and chunk.finish_reason is None:
                if chunk.message.content:
                    content_buffer += chunk.message.content
                    chunks_with_content += 1

                if content_buffer:
                    yield self.create_assistant_event(content_buffer, chunk)
                    content_buffer = ""
                    chunks_with_content = 0
                continue

            if chunk.message.content:
                content_buffer += chunk.message.content
                chunks_with_content += 1

                if chunks_with_content >= BATCH_SIZE:
                    yield self.create_assistant_event(content_buffer, chunk)
                    content_buffer = ""
                    chunks_with_content = 0

        if content_buffer:
            last_chunk = chunks[-1] if chunks else None
            yield self.create_assistant_event(content_buffer, last_chunk)

        full_content = ""
        full_tool_calls_map = OrderedDict[int, ToolCall]()
        for chunk in chunks:
            full_content += chunk.message.content or ""
            if not chunk.message.tool_calls:
                continue

            for tc in chunk.message.tool_calls:
                if tc.index is None:
                    raise LLMResponseError("Tool call chunk missing index")
                if tc.index not in full_tool_calls_map:
                    full_tool_calls_map[tc.index] = tc
                else:
                    new_args_str = (
                        full_tool_calls_map[tc.index].function.arguments or ""
                    ) + (tc.function.arguments or "")
                    full_tool_calls_map[tc.index].function.arguments = new_args_str

        full_tool_calls = list(full_tool_calls_map.values()) or None
        last_message = LLMMessage(
            role=Role.assistant, content=full_content, tool_calls=full_tool_calls
        )
        # We don't append to self.messages here because this is LLMClient
        # The caller (Agent) is responsible for appending the final message to history

        finish_reason = next(
            (c.finish_reason for c in chunks if c.finish_reason is not None), None
        )
        self.last_chunk = LLMChunk(
            message=last_message, usage=chunks[-1].usage, finish_reason=finish_reason
        )
