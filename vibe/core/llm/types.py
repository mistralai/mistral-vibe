from __future__ import annotations

from collections.abc import AsyncGenerator
import types
from typing import TYPE_CHECKING, Protocol

from vibe.core.types import AvailableTool, LLMChunk, LLMMessage, StrToolChoice

if TYPE_CHECKING:
    from vibe.core.config import ModelConfig


class BackendLike(Protocol):
    """Port protocol for dependency-injectable LLM backends.

    Any backend used by Agent should implement this async context manager
    interface with `complete`, `complete_streaming` and `count_tokens` methods.
    """

    async def __aenter__(self) -> BackendLike: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None: ...

    async def complete(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        extra_headers: dict[str, str] | None,
    ) -> LLMChunk:
        """Complete a chat conversation using the specified model and provider.

        Args:
            model: Model configuration
            messages: List of conversation messages
            temperature: Sampling temperature (0.0 to 1.0)
            tools: Optional list of available tools
            max_tokens: Maximum tokens to generate
            tool_choice: How to choose tools (auto, none, or specific tool)
            extra_headers: Additional HTTP headers to include

        Returns:
            LLMChunk containing the response message and usage information

        Raises:
            BackendError: If the API request fails
        """
        ...

    # Note: actual implementation should be an async function,
    # but we can't make this one async, as it would lead to wrong type inference
    # https://stackoverflow.com/a/68911014
    def complete_streaming(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float,
        tools: list[AvailableTool] | None,
        max_tokens: int | None,
        tool_choice: StrToolChoice | AvailableTool | None,
        extra_headers: dict[str, str] | None,
    ) -> AsyncGenerator[LLMChunk, None]:
        """Equivalent of the complete method, but yields LLMEvent objects
        instead of a single LLMEvent.

        Args:
            model: Model configuration
            messages: List of conversation messages
            temperature: Sampling temperature (0.0 to 1.0)
            tools: Optional list of available tools
            max_tokens: Maximum tokens to generate
            tool_choice: How to choose tools (auto, none, or specific tool)
            extra_headers: Additional HTTP headers to include

        Returns:
            AsyncGenerator[LLMEvent, None] yielding LLMEvent objects

        Raises:
            BackendError: If the API request fails
        """
        ...

    async def count_tokens(
        self,
        *,
        model: ModelConfig,
        messages: list[LLMMessage],
        temperature: float = 0.0,
        tools: list[AvailableTool] | None,
        tool_choice: StrToolChoice | AvailableTool | None = None,
        extra_headers: dict[str, str] | None,
    ) -> int:
        """Count the number of tokens in the prompt without generating a real response.

        This is useful for:
        - Determining system prompt token count
        - Checking context size after compaction
        - Pre-flight token validation

        Args:
            model: Model configuration
            messages: List of messages to count tokens for
            temperature: Sampling temperature
            tools: Optional list of available tools
            tool_choice: How to choose tools
            extra_headers: Additional HTTP headers to include

        Returns:
            The number of prompt tokens
        """
        ...
