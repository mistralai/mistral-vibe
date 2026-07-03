from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, final

import httpx
from mistralai.client import Mistral
from mistralai.client.errors import SDKError
from mistralai.client.models import (
    ConversationResponse,
    MessageOutputEntry,
    TextChunk,
    ToolReferenceChunk,
)
from pydantic import BaseModel, Field

from vibe.core.config import DEFAULT_MISTRAL_API_ENV_KEY, VibeConfig, resolve_api_key
from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    InvokeContext,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData
from vibe.core.types import ToolStreamEvent
from vibe.core.utils.http import build_ssl_context, get_server_url_from_api_base

if TYPE_CHECKING:
    from vibe.core.types import ToolCallEvent, ToolResultEvent


class WebSearchSource(BaseModel):
    title: str
    url: str


class WebSearchArgs(BaseModel):
    query: str = Field(min_length=1, description="The search query")


class WebSearchResult(BaseModel):
    query: str
    answer: str
    sources: list[WebSearchSource] = Field(default_factory=list)


class WebSearchConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK
    timeout: int = Field(default=120, description="HTTP timeout in seconds.")
    model: str = Field(
        default="mistral-vibe-cli-with-tools",
        description="Mistral model to use for web search.",
    )


class WebSearch(
    BaseTool[WebSearchArgs, WebSearchResult, WebSearchConfig, BaseToolState],
    ToolUIData[WebSearchArgs, WebSearchResult],
):
    @classmethod
    def is_available(cls, config: VibeConfig | None = None) -> bool:
        if config is None:
            return bool(resolve_api_key(DEFAULT_MISTRAL_API_ENV_KEY))

        provider = config.get_mistral_provider()
        if provider is None:
            return bool(resolve_api_key(DEFAULT_MISTRAL_API_ENV_KEY))

        return bool(resolve_api_key(cls._api_key_env_var(config)))

    @final
    async def run(
        self, args: WebSearchArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | WebSearchResult, None]:
        config = self._resolve_config(ctx)
        api_key_env_var = self._api_key_env_var(config)
        api_key = resolve_api_key(api_key_env_var)
        if not api_key:
            raise ToolError(f"{api_key_env_var} environment variable not set.")

        ssl_context = build_ssl_context()
        async_http_client = httpx.AsyncClient(follow_redirects=True, verify=ssl_context)

        try:
            client = Mistral(
                api_key=api_key,
                server_url=self._resolve_server_url(ctx),
                timeout_ms=self.config.timeout * 1000,
                async_client=async_http_client,
            )
            async with async_http_client, client:
                response = await client.beta.conversations.start_async(
                    model=self.config.model,
                    instructions="Always use the web_search tool to answer queries. Never answer from memory alone.",
                    tools=[{"type": "web_search"}],
                    inputs=args.query,
                    store=False,
                )

                yield self._parse_response(response, args.query)

        except SDKError as exc:
            raise ToolError(f"Mistral API error: {exc}") from exc
        finally:
            await async_http_client.aclose()

    def _resolve_server_url(self, ctx: InvokeContext | None) -> str | None:
        config = self._resolve_config(ctx)
        if config is None:
            return None
        provider = config.get_mistral_provider()
        if provider is None:
            return None
        return get_server_url_from_api_base(provider.api_base)

    def _resolve_config(self, ctx: InvokeContext | None) -> VibeConfig | None:
        if not ctx or not ctx.agent_manager:
            return None
        return ctx.agent_manager.config

    @classmethod
    def _api_key_env_var(cls, config: VibeConfig | None) -> str:
        if config is None:
            return DEFAULT_MISTRAL_API_ENV_KEY
        provider = config.get_mistral_provider()
        if provider is None:
            return DEFAULT_MISTRAL_API_ENV_KEY
        return provider.api_key_env_var or DEFAULT_MISTRAL_API_ENV_KEY

    def _parse_response(
        self, response: ConversationResponse, query: str
    ) -> WebSearchResult:
        text_parts: list[str] = []
        sources: dict[str, WebSearchSource] = {}

        for entry in response.outputs:
            if not isinstance(entry, MessageOutputEntry):
                continue
            # content is a plain string for short answers, else a list of chunks.
            if isinstance(entry.content, str):
                text_parts.append(entry.content)
                continue
            for chunk in entry.content:
                if isinstance(chunk, TextChunk):
                    text_parts.append(chunk.text)
                elif isinstance(chunk, ToolReferenceChunk) and chunk.url:
                    if chunk.url not in sources:
                        sources[chunk.url] = WebSearchSource(
                            title=chunk.title, url=chunk.url
                        )

        answer = "".join(text_parts).strip()
        if not answer:
            raise ToolError("No text in agent response.")

        return WebSearchResult(
            query=query, answer=answer, sources=list(sources.values())
        )

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if event.args is None:
            return ToolCallDisplay(summary="web_search")
        if not isinstance(event.args, WebSearchArgs):
            return ToolCallDisplay(summary="web_search")
        return ToolCallDisplay(summary=f"Searching the web: {event.args.query!r}")

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, WebSearchResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )
        source_count = len(event.result.sources)
        plural = "" if source_count == 1 else "s"
        message = f"Searched {event.result.query!r} ({source_count} source{plural})"
        return ToolResultDisplay(success=True, message=message)

    @classmethod
    def get_status_text(cls) -> str:
        return "Searching the web"
