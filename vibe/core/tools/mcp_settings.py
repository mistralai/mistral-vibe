"""Persist MCP server and connector enable/disable settings.

Shared by all entrypoints (CLI, ACP, etc.) so toggle logic is not
tied to a particular UI layer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from ipaddress import ip_address
import re
from typing import Any, Literal, cast
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import BaseModel, ValidationError

from vibe.core.config import (
    MCPHttp,
    MCPOAuth,
    MCPServer,
    MCPStreamableHttp,
    VibeConfigSchema,
)
from vibe.core.config.orchestrator import ConfigOrchestrator

MCPAddTransport = Literal["http", "streamable-http"]


class MCPServerAddError(ValueError):
    pass


@dataclass(frozen=True)
class MCPServerAddResult:
    name: str
    url: str
    created: bool


_DEFAULT_PORTS = {"http": 80, "https": 443}
_HOST_PREFIXES_TO_DROP = {"mcp", "www"}
_GENERIC_ALIAS_SEGMENTS = {"api", "mcp", "server"}
_LEADING_HOST_PREFIX_LABEL_MIN_COUNT = 3


def updated_tool_list(tools: list[str], name: str, disabled: bool) -> list[str]:
    """Return a new disabled_tools list with *name* added or removed (unique)."""
    if disabled:
        return list(dict.fromkeys([*tools, name]))
    return [t for t in tools if t != name]


async def persist_mcp_toggle(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    *,
    name: str,
    is_connector: bool,
    disabled: bool,
    tool_name: str | None = None,
) -> None:
    """Save an MCP server/connector or individual tool toggle to the config file."""
    if is_connector:
        await _persist_connector_toggle(
            orchestrator, name=name, disabled=disabled, tool_name=tool_name
        )
    else:
        await _persist_server_toggle(
            orchestrator, name=name, disabled=disabled, tool_name=tool_name
        )


async def _persist_server_toggle(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    *,
    name: str,
    disabled: bool,
    tool_name: str | None,
) -> None:
    servers = _dump_persisted(orchestrator.config.mcp_servers)
    for s in servers:
        if s.get("name") == name:
            if tool_name is not None:
                s["disabled_tools"] = updated_tool_list(
                    s.get("disabled_tools", []), tool_name, disabled
                )
            else:
                s["disabled"] = disabled
            break
    else:
        # Server not in base config (profile-only) -- nothing to persist.
        return
    await orchestrator.set_field("/mcp_servers", servers)


async def _persist_connector_toggle(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    *,
    name: str,
    disabled: bool,
    tool_name: str | None,
) -> None:
    connectors = _dump_persisted(orchestrator.config.connectors)
    for c in connectors:
        if c.get("name") == name:
            if tool_name is not None:
                c["disabled_tools"] = updated_tool_list(
                    c.get("disabled_tools", []), tool_name, disabled
                )
            else:
                c["disabled"] = disabled
            break
    else:
        entry: dict[str, Any] = {"name": name}
        if tool_name is not None:
            entry["disabled_tools"] = [tool_name] if disabled else []
        else:
            entry["disabled"] = disabled
        connectors.append(entry)
    await orchestrator.set_field("/connectors", connectors)


async def persist_oauth_mcp_server(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    *,
    url: str,
    name: str | None = None,
    scopes: list[str] | None = None,
    transport: MCPAddTransport = "streamable-http",
) -> MCPServerAddResult:
    normalized_url = _normalize_mcp_oauth_url(url)
    normalized_url_key = _url_key(normalized_url)
    requested_name = _normalize_server_name(name) if name is not None else None
    if name is not None and not requested_name:
        raise MCPServerAddError("MCP server name must contain letters or numbers.")

    active_servers = list(orchestrator.config.mcp_servers)
    if result := _find_active_url_match(
        active_servers, normalized_url_key, requested_name
    ):
        return result

    raw_servers = _dump_persisted(orchestrator.config.mcp_servers)
    if result := _find_persisted_url_match(
        raw_servers, normalized_url_key, requested_name
    ):
        return result

    raw_names = _persisted_server_names(raw_servers)
    active_names = {server.name for server in active_servers} | raw_names
    server_name = _resolve_new_server_name(requested_name, normalized_url, active_names)

    entry: dict[str, Any] = {
        "name": server_name,
        "transport": transport,
        "url": normalized_url,
        "auth": {"type": "oauth", "scopes": scopes or []},
    }
    try:
        model = MCPHttp if transport == "http" else MCPStreamableHttp
        model.model_validate(entry)
    except ValidationError as exc:
        raise MCPServerAddError(f"Invalid MCP server configuration: {exc}") from exc

    await orchestrator.set_field("/mcp_servers", [*raw_servers, entry])
    return MCPServerAddResult(name=server_name, url=normalized_url, created=True)


def parse_mcp_add_transport(value: str) -> MCPAddTransport:
    match value:
        case "http" | "streamable-http":
            return value
        case _:
            raise MCPServerAddError(
                "MCP server transport must be one of: http, streamable-http."
            )


def _find_active_url_match(
    servers: list[MCPServer], normalized_url_key: str, requested_name: str | None
) -> MCPServerAddResult | None:
    for server in servers:
        if server.transport not in {"http", "streamable-http"}:
            continue
        http_server = cast("MCPHttp | MCPStreamableHttp", server)
        if _url_key(http_server.url) != normalized_url_key:
            continue
        if not isinstance(http_server.auth, MCPOAuth):
            raise MCPServerAddError(
                f"MCP server URL is already configured as `{http_server.name}` "
                "with static auth. `/mcp add` only supports OAuth MCP servers."
            )
        if requested_name is not None and requested_name != http_server.name:
            raise MCPServerAddError(
                f"MCP server URL is already configured as `{http_server.name}`."
            )
        return MCPServerAddResult(
            name=http_server.name, url=http_server.url, created=False
        )
    return None


def _dump_persisted(items: Iterable[BaseModel]) -> list[dict[str, Any]]:
    # NOTE: reads the merged config, not just the persisted TOML layer, so entries
    # injected via env/args leak in. Acceptable for now; revisit if that matters.
    return [item.model_dump(mode="json", exclude_none=True) for item in items]


def _find_persisted_url_match(
    raw_servers: list[dict[str, Any]],
    normalized_url_key: str,
    requested_name: str | None,
) -> MCPServerAddResult | None:
    for raw_server in raw_servers:
        if raw_server.get("transport") not in {"http", "streamable-http"}:
            continue
        raw_url = raw_server.get("url")
        if not isinstance(raw_url, str) or _url_key(raw_url) != normalized_url_key:
            continue
        raw_name = raw_server.get("name")
        if not isinstance(raw_name, str):
            raw_name = _suggest_server_name(raw_url)
        if not _is_persisted_oauth_server(raw_server):
            raise MCPServerAddError(
                f"MCP server URL is already configured as `{raw_name}` "
                "with static auth. `/mcp add` only supports OAuth MCP servers."
            )
        if requested_name is not None and requested_name != raw_name:
            raise MCPServerAddError(
                f"MCP server URL is already configured as `{raw_name}`."
            )
        return MCPServerAddResult(name=raw_name, url=raw_url, created=False)
    return None


def _is_persisted_oauth_server(raw_server: dict[str, Any]) -> bool:
    auth = raw_server.get("auth")
    return isinstance(auth, dict) and auth.get("type") == "oauth"


def _persisted_server_names(raw_servers: list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for raw_server in raw_servers:
        name = raw_server.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def _resolve_new_server_name(
    requested_name: str | None, normalized_url: str, active_names: set[str]
) -> str:
    if requested_name is None:
        return _dedupe_server_name(_suggest_server_name(normalized_url), active_names)

    if requested_name in active_names:
        raise MCPServerAddError(
            f"MCP server name `{requested_name}` is already configured."
        )
    return requested_name


def _normalize_mcp_oauth_url(value: str) -> str:
    raw_url = value.strip()
    if not raw_url:
        raise MCPServerAddError("MCP server URL is required.")

    parsed = urlsplit(raw_url)
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if not scheme:
        raise MCPServerAddError("MCP server URL must include a scheme.")
    if scheme not in {"http", "https"}:
        raise MCPServerAddError("MCP server URL must use https.")
    if not host:
        raise MCPServerAddError("MCP server URL must include a host.")
    if parsed.fragment:
        raise MCPServerAddError("MCP server URL must not include a fragment.")
    if scheme == "http" and not _is_loopback_host(host):
        raise MCPServerAddError(
            "MCP server URL must use https unless it points to localhost."
        )

    return _url_with_normalized_host(parsed, trim_trailing_slash=False)


def _url_key(value: str) -> str:
    parsed = urlsplit(value.strip())
    return _url_with_normalized_host(parsed, trim_trailing_slash=True)


def _url_with_normalized_host(parsed: SplitResult, *, trim_trailing_slash: bool) -> str:
    scheme = parsed.scheme.lower()
    host = parsed.hostname
    if host is None:
        return parsed.geturl()

    hostname = host.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"

    netloc = hostname
    if parsed.port is not None and parsed.port != _DEFAULT_PORTS.get(scheme):
        netloc = f"{netloc}:{parsed.port}"

    path = parsed.path
    if trim_trailing_slash:
        path = path.rstrip("/")

    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _suggest_server_name(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or "mcp"
    labels = [label for label in host.lower().split(".") if label]
    if (
        len(labels) >= _LEADING_HOST_PREFIX_LABEL_MIN_COUNT
        and labels[0] in _HOST_PREFIXES_TO_DROP
    ):
        labels = labels[1:]

    candidate = labels[0] if labels else ""
    if candidate in _GENERIC_ALIAS_SEGMENTS:
        candidate = _path_alias_candidate(parsed.path)

    return _normalize_server_name(candidate) or "mcp"


def _path_alias_candidate(path: str) -> str:
    for segment in path.split("/"):
        normalized = _normalize_server_name(segment.lower())
        if normalized and normalized not in _GENERIC_ALIAS_SEGMENTS:
            return normalized
    return ""


def _normalize_server_name(value: str | None) -> str:
    if not value:
        return ""
    normalized = re.sub(r"[^a-zA-Z0-9_-]", "_", value)
    normalized = normalized.strip("_-")
    return normalized[:256]


def _dedupe_server_name(base: str, existing_names: set[str]) -> str:
    if base not in existing_names:
        return base

    index = 2
    while True:
        candidate = f"{base}_{index}"
        if candidate not in existing_names:
            return candidate
        index += 1
