"""Web fetch tool for retrieving URL content.

This tool allows the LLM to fetch content from URLs.
"""

from __future__ import annotations

import asyncio
import re
from typing import TYPE_CHECKING, ClassVar, final
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.ui import ToolCallDisplay, ToolResultDisplay, ToolUIData

if TYPE_CHECKING:
    from vibe.core.types import ToolCallEvent, ToolResultEvent


class WebFetchArgs(BaseModel):
    """Arguments for fetching a URL."""

    url: str = Field(
        description="The URL to fetch content from."
    )
    extract_text: bool = Field(
        default=True,
        description="Extract readable text from HTML (removes scripts, styles, etc.).",
    )
    include_headers: bool = Field(
        default=False,
        description="Include response headers in the result.",
    )


class WebFetchResult(BaseModel):
    """Result of fetching a URL."""

    url: str
    status_code: int
    content_type: str | None
    content: str
    content_length: int
    was_truncated: bool
    headers: dict[str, str] | None = None
    title: str | None = None  # Extracted page title for HTML


class WebFetchConfig(BaseToolConfig):
    """Configuration for web_fetch tool."""

    permission: ToolPermission = ToolPermission.ASK

    max_content_bytes: int = Field(
        default=100_000,
        description="Maximum content size to return (in bytes).",
    )
    timeout_seconds: int = Field(
        default=30,
        description="Request timeout in seconds.",
    )
    allowed_schemes: list[str] = Field(
        default=["http", "https"],
        description="Allowed URL schemes.",
    )
    blocked_domains: list[str] = Field(
        default=[],
        description="Domains to block requests to.",
    )
    user_agent: str = Field(
        default="MistralVibe/1.0 (AI Assistant)",
        description="User-Agent header for requests.",
    )


class WebFetchState(BaseToolState):
    """State for web_fetch tool."""

    fetch_history: list[str] = Field(default_factory=list)


class WebFetch(
    BaseTool[WebFetchArgs, WebFetchResult, WebFetchConfig, WebFetchState],
    ToolUIData[WebFetchArgs, WebFetchResult],
):
    """Fetch content from a URL."""

    description: ClassVar[str] = (
        "Fetch content from a URL. Can retrieve web pages, API responses, "
        "documentation, and other web content. For HTML pages, extracts "
        "readable text by default."
    )

    @classmethod
    def get_name(cls) -> str:
        return "web_fetch"

    @final
    async def run(self, args: WebFetchArgs) -> WebFetchResult:
        self._validate_url(args.url)

        # Track in state
        self.state.fetch_history.append(args.url)
        if len(self.state.fetch_history) > 20:
            self.state.fetch_history.pop(0)

        return await self._fetch_url(args)

    def _validate_url(self, url: str) -> None:
        """Validate the URL is allowed."""
        try:
            parsed = urlparse(url)
        except Exception as exc:
            raise ToolError(f"Invalid URL: {exc}") from exc

        if not parsed.scheme:
            raise ToolError("URL must include a scheme (http:// or https://)")

        if parsed.scheme not in self.config.allowed_schemes:
            raise ToolError(
                f"URL scheme '{parsed.scheme}' not allowed. "
                f"Allowed: {', '.join(self.config.allowed_schemes)}"
            )

        if not parsed.netloc:
            raise ToolError("URL must include a domain")

        # Check blocked domains
        domain = parsed.netloc.lower()
        for blocked in self.config.blocked_domains:
            if blocked in domain:
                raise ToolError(f"Domain '{domain}' is blocked")

    async def _fetch_url(self, args: WebFetchArgs) -> WebFetchResult:
        """Fetch the URL content."""
        try:
            import httpx
        except ImportError:
            raise ToolError(
                "httpx is required for web_fetch. Install with: pip install httpx"
            )

        try:
            async with httpx.AsyncClient(
                timeout=self.config.timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": self.config.user_agent},
            ) as client:
                response = await client.get(args.url)

                content_type = response.headers.get("content-type", "")
                raw_content = response.text

                # Extract text from HTML if requested
                title = None
                if args.extract_text and "text/html" in content_type.lower():
                    title = self._extract_title(raw_content)
                    raw_content = self._extract_text_from_html(raw_content)

                # Truncate if needed
                was_truncated = len(raw_content) > self.config.max_content_bytes
                content = raw_content[: self.config.max_content_bytes]
                if was_truncated:
                    content += "\n\n... [content truncated]"

                headers = None
                if args.include_headers:
                    headers = dict(response.headers)

                return WebFetchResult(
                    url=str(response.url),  # May differ due to redirects
                    status_code=response.status_code,
                    content_type=content_type,
                    content=content,
                    content_length=len(raw_content),
                    was_truncated=was_truncated,
                    headers=headers,
                    title=title,
                )

        except asyncio.TimeoutError:
            raise ToolError(
                f"Request timed out after {self.config.timeout_seconds} seconds"
            )
        except Exception as exc:
            raise ToolError(f"Failed to fetch URL: {exc}") from exc

    def _extract_title(self, html: str) -> str | None:
        """Extract title from HTML."""
        match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return None

    def _extract_text_from_html(self, html: str) -> str:
        """Extract readable text from HTML."""
        # Remove script and style elements
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<head[^>]*>.*?</head>", "", html, flags=re.DOTALL | re.IGNORECASE)

        # Remove HTML comments
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

        # Replace common block elements with newlines
        html = re.sub(r"<(?:p|div|br|h[1-6]|li|tr)[^>]*>", "\n", html, flags=re.IGNORECASE)

        # Remove remaining HTML tags
        html = re.sub(r"<[^>]+>", " ", html)

        # Decode common HTML entities
        html = html.replace("&nbsp;", " ")
        html = html.replace("&amp;", "&")
        html = html.replace("&lt;", "<")
        html = html.replace("&gt;", ">")
        html = html.replace("&quot;", '"')
        html = html.replace("&#39;", "'")

        # Clean up whitespace
        html = re.sub(r"[ \t]+", " ", html)
        html = re.sub(r"\n\s*\n", "\n\n", html)

        return html.strip()

    @classmethod
    def get_call_display(cls, event: ToolCallEvent) -> ToolCallDisplay:
        args = event.args
        if not isinstance(args, WebFetchArgs):
            return ToolCallDisplay(summary="web_fetch")

        # Parse URL for display
        try:
            parsed = urlparse(args.url)
            display_url = parsed.netloc + (parsed.path[:30] + "..." if len(parsed.path) > 30 else parsed.path)
        except Exception:
            display_url = args.url[:50]

        return ToolCallDisplay(
            summary=f"fetch: {display_url}",
            details={
                "url": args.url,
                "extract_text": args.extract_text,
            },
        )

    @classmethod
    def get_result_display(cls, event: ToolResultEvent) -> ToolResultDisplay:
        if not isinstance(event.result, WebFetchResult):
            return ToolResultDisplay(
                success=False,
                message=event.error or event.skip_reason or "No result",
            )

        result = event.result

        # Determine success based on status code
        success = 200 <= result.status_code < 400

        message = f"HTTP {result.status_code}"
        if result.title:
            message += f" - {result.title[:50]}"

        size_str = _format_size(result.content_length)
        message += f" ({size_str})"

        if result.was_truncated:
            message += " [truncated]"

        warnings = []
        if result.was_truncated:
            warnings.append("Content was truncated due to size limit")
        if result.status_code >= 400:
            warnings.append(f"Server returned error status {result.status_code}")

        return ToolResultDisplay(
            success=success,
            message=message,
            warnings=warnings,
            details={
                "url": result.url,
                "status_code": result.status_code,
                "content_type": result.content_type,
                "content_preview": result.content[:500] + "..." if len(result.content) > 500 else result.content,
            },
        )

    @classmethod
    def get_status_text(cls) -> str:
        return "Fetching URL"


def _format_size(size: int) -> str:
    """Format size in human-readable form."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}{unit}"
        size /= 1024
    return f"{size:.1f}TB"
