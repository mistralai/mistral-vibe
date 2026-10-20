from __future__ import annotations

import httpx
from pydantic import BaseModel, Field

from vibe.core.tools.base import (
    BaseTool,
    BaseToolConfig,
    BaseToolState,
    ToolError,
    ToolPermission,
)
from vibe.core.tools.permissions import (
    PermissionContext,
    PermissionScope,
    RequiredPermission,
)


def _url_origin(url: str) -> str:
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as error:
        raise ToolError(f"Invalid redirect URL: {url}") from error

    if parsed.scheme not in {"http", "https"} or not parsed.raw_host:
        raise ToolError(f"Invalid redirect URL: {url}")

    host = parsed.raw_host.decode("ascii")
    if ":" in host:
        host = f"[{host}]"

    default_port = 443 if parsed.scheme == "https" else 80
    port_suffix = "" if parsed.port in {None, default_port} else f":{parsed.port}"
    return f"{parsed.scheme}://{host}{port_suffix}"


class WebFetchArgs(BaseModel):
    url: str = Field(description="The URL to fetch content from")
    timeout: int | None = Field(
        default=None, description="Optional timeout in seconds (max 120)"
    )


class WebFetchResult(BaseModel):
    url: str
    content: str
    content_type: str
    was_truncated: bool = False


class WebFetchConfig(BaseToolConfig):
    permission: ToolPermission = ToolPermission.ASK

    default_timeout: int = Field(default=30, description="Default timeout in seconds.")
    max_timeout: int = Field(default=120, description="Maximum allowed timeout.")
    max_content_bytes: int = Field(
        default=120_000,
        description="Maximum content size in bytes returned to the model.",
    )
    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        description="User agent string for requests.",
    )


class WebFetch(BaseTool[WebFetchArgs, WebFetchResult, WebFetchConfig, BaseToolState]):
    @staticmethod
    def _normalize_url(url: str) -> str:
        """Normalise a URL to always have an http(s) scheme.

        Handles protocol-relative URLs (//example.com) and bare URLs (example.com).
        """
        raw = url.lstrip("/") if url.startswith("//") else url
        return raw if raw.startswith(("http://", "https://")) else "https://" + raw

    def resolve_permission(self, args: WebFetchArgs) -> PermissionContext | None:
        if self.config.permission in {ToolPermission.ALWAYS, ToolPermission.NEVER}:
            return PermissionContext(permission=self.config.permission)

        normalized_url = self._normalize_url(args.url)
        try:
            origin = _url_origin(normalized_url)
        except ToolError:
            return None

        return PermissionContext(
            permission=ToolPermission.ASK,
            required_permissions=[
                RequiredPermission(
                    scope=PermissionScope.URL_PATTERN,
                    invocation_pattern=origin,
                    session_pattern=origin,
                    label=f"fetching from {origin}",
                )
            ],
        )
