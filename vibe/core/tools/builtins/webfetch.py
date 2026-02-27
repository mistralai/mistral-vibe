from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, ClassVar, final
from urllib.parse import urlparse

import httpx
from markdownify import MarkdownConverter
from pydantic import BaseModel, Field

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

if TYPE_CHECKING:
    from vibe.core.types import ToolCallEvent, ToolResultEvent


_HONEST_USER_AGENT = "vibe-cli"
_HTTP_FORBIDDEN = 403


class _Converter(MarkdownConverter):
    convert_script = convert_style = convert_noscript = convert_iframe = (
        convert_object
    ) = convert_embed = lambda *_, **__: ""


class WebFetchArgs(BaseModel):
    url: str = Field(description="URL to fetch (http/https)")
    timeout: int | None = Field(
        default=None, description="Timeout in seconds (max 120)"
    )


class WebFetchResult(BaseModel):
    url: str
    content: str
    content_type: str


class WebFetchConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK

    default_timeout: int = Field(default=30, description="Default timeout in seconds.")
    max_timeout: int = Field(default=120, description="Maximum allowed timeout.")
    max_content_bytes: int = Field(
        default=512_000, description="Maximum content size to fetch."
    )
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        description="User agent string for requests.",
    )


class WebFetch(
    BaseTool[WebFetchArgs, WebFetchResult, WebFetchConfig, BaseToolState],
    ToolUIData[WebFetchArgs, WebFetchResult],
):
    description: ClassVar[str] = (
        "Fetch content from a URL. Converts HTML to markdown for readability."
    )

    @final
    async def run(
        self, args: WebFetchArgs, ctx: InvokeContext | None = None
    ) -> AsyncGenerator[ToolStreamEvent | WebFetchResult, None]:
        self._validate_args(args)

        raw = args.url.lstrip("/") if args.url.startswith("//") else args.url
        url = raw if raw.startswith(("http://", "https://")) else "https://" + raw
        timeout = self._resolve_timeout(args.timeout)

        content, content_type = await self._fetch_url(url, timeout)

        if "text/html" in content_type:
            content = _html_to_markdown(content)

        yield WebFetchResult(url=url, content=content, content_type=content_type)

    def _validate_args(self, args: WebFetchArgs) -> None:
        if not args.url.strip():
            raise ToolError("URL cannot be empty")

        parsed = urlparse(args.url)
        if parsed.scheme and parsed.scheme not in {"http", "https"}:
            raise ToolError(
                f"Invalid URL scheme: {parsed.scheme}. Must be http or https."
            )

        if args.timeout is not None:
            if args.timeout <= 0:
                raise ToolError("Timeout must be a positive number")
            if args.timeout > self.config.max_timeout:
                raise ToolError(
                    f"Timeout cannot exceed {self.config.max_timeout} seconds"
                )

    def _resolve_timeout(self, timeout: int | None) -> int:
        if timeout is None:
            return self.config.default_timeout
        return min(timeout, self.config.max_timeout)

    async def _fetch_url(self, url: str, timeout: int) -> tuple[str, str]:
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,application/xml;q=0.9,"
                "image/avif,image/webp,image/apng,*/*;q=0.8"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        }

        try:
            response = await self._do_fetch(url, timeout, headers)
        except httpx.TimeoutException:
            raise ToolError(f"Request timed out after {timeout} seconds")
        except httpx.RequestError as e:
            raise ToolError(f"Failed to fetch URL: {e}")

        if response.is_error:
            raise ToolError(
                f"HTTP error {response.status_code}: {response.reason_phrase}"
            )

        content_type = response.headers.get("Content-Type", "text/plain")

        content_bytes = response.content[: self.config.max_content_bytes]
        content = content_bytes.decode("utf-8", errors="ignore")

        if len(response.content) > self.config.max_content_bytes:
            content += "[Content truncated due to size limit]"

        return content, content_type

    async def _do_fetch(
        self, url: str, timeout: int, headers: dict[str, str]
    ) -> httpx.Response:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=httpx.Timeout(timeout)
        ) as client:
            response = await client.get(url, headers=headers)

            # In case we are hitting bot detection retry once honestly
            if (
                response.status_code == _HTTP_FORBIDDEN
                and response.headers.get("cf-mitigated") == "challenge"
            ):
                headers["User-Agent"] = _HONEST_USER_AGENT
                response = await client.get(url, headers=headers)

            return response

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        if event.args is None:
            return ToolCallDisplay(summary="webfetch")
        if not isinstance(event.args, WebFetchArgs):
            return ToolCallDisplay(summary="webfetch")

        parsed = urlparse(event.args.url)
        domain = parsed.netloc or event.args.url[:50]
        summary = f"Fetching: {domain}"

        if event.args.timeout:
            summary += f" (timeout {event.args.timeout}s)"

        return ToolCallDisplay(summary=summary)

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, WebFetchResult):
            return ToolResultDisplay(
                success=False, message=event.error or event.skip_reason or "No result"
            )

        content_len = len(event.result.content)
        message = (
            f"Fetched {content_len:,} chars ({event.result.content_type.split(';')[0]})"
        )

        return ToolResultDisplay(success=True, message=message)

    @classmethod
    def get_status_text(cls) -> str:
        return "Fetching URL"


def _html_to_markdown(html: str) -> str:
    return _Converter(heading_style="ATX", bullets="-").convert(html)
