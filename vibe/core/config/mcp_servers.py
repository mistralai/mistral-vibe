from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import SplitResult, urlsplit, urlunsplit

from pydantic import TypeAdapter, ValidationError

from vibe.core.config.models import (
    MCPHttp,
    MCPOAuth,
    MCPServer,
    MCPStdio,
    MCPStreamableHttp,
    normalize_mcp_server_name,
)
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.types import ConcurrencyConflictError
from vibe.core.config.vibe_schema import VibeConfigSchema
from vibe.utils.mcp import MCPAddTransport


class MCPServerAddError(ValueError):
    pass


class MCPServerRemoveError(ValueError):
    pass


@dataclass(frozen=True)
class PersistedMCPServerResult[ServerT: MCPHttp | MCPStreamableHttp | MCPStdio]:
    server: ServerT
    created: bool


@dataclass(frozen=True)
class RemovedMCPServerResult:
    name: str
    server: MCPServer | None
    removed: bool


_DEFAULT_PORTS = {"http": 80, "https": 443}
_HOST_PREFIXES_TO_DROP = {"mcp", "www"}
_GENERIC_ALIAS_SEGMENTS = {"api", "mcp", "server"}
_LEADING_HOST_PREFIX_LABEL_MIN_COUNT = 3
_MCP_SERVER_ADAPTER = TypeAdapter(MCPServer)


async def persist_oauth_mcp_server(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    *,
    url: str,
    name: str | None = None,
    scopes: list[str] | None = None,
    transport: MCPAddTransport = "streamable-http",
) -> PersistedMCPServerResult[MCPHttp | MCPStreamableHttp]:
    normalized_url = normalize_mcp_server_url(url)
    requested_name = normalize_mcp_server_name(name) if name is not None else None
    if name is not None and not requested_name:
        raise MCPServerAddError("MCP server name must contain letters or numbers.")

    active_servers = list(orchestrator.config.mcp_servers)
    if existing := _find_server_url(active_servers, normalized_url):
        if not isinstance(existing.auth, MCPOAuth):
            raise MCPServerAddError(
                f"MCP server URL is already configured as `{existing.name}` "
                "with static auth. `/mcp add` only supports OAuth MCP servers."
            )
        if requested_name is not None and requested_name != existing.name:
            raise MCPServerAddError(
                f"MCP server URL is already configured as `{existing.name}`."
            )
        return PersistedMCPServerResult(server=existing, created=False)

    server_name = _resolve_new_server_name(
        requested_name, normalized_url, {server.name for server in active_servers}
    )
    model = MCPHttp if transport == "http" else MCPStreamableHttp
    try:
        server = model.model_validate({
            "name": server_name,
            "transport": transport,
            "url": normalized_url,
            "auth": {"type": "oauth", "scopes": scopes or []},
        })
    except ValidationError as exc:
        raise MCPServerAddError(f"Invalid MCP server configuration: {exc}") from exc
    return await persist_remote_mcp_server(orchestrator, server)


async def persist_remote_mcp_server(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    server: MCPHttp | MCPStreamableHttp,
) -> PersistedMCPServerResult[MCPHttp | MCPStreamableHttp]:
    normalized_server = server.model_copy(
        update={"url": normalize_mcp_server_url(server.url)}
    )
    current_servers = list(orchestrator.config.mcp_servers)
    if existing := _find_server_name(current_servers, normalized_server.name):
        if isinstance(existing, MCPHttp | MCPStreamableHttp):
            if _remote_servers_equivalent(existing, normalized_server):
                return PersistedMCPServerResult(server=existing, created=False)
            if _url_key(existing.url) == _url_key(normalized_server.url):
                raise MCPServerAddError(
                    f"MCP server `{normalized_server.name}` is already configured "
                    "with different options."
                )
        raise MCPServerAddError(
            f"MCP server name `{normalized_server.name}` is already configured."
        )
    if existing := _find_server_url(current_servers, normalized_server.url):
        raise MCPServerAddError(
            f"MCP server URL is already configured as `{existing.name}`."
        )

    await _append_persisted_mcp_server(orchestrator, normalized_server)
    return PersistedMCPServerResult(server=normalized_server, created=True)


async def persist_stdio_mcp_server(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], server: MCPStdio
) -> PersistedMCPServerResult[MCPStdio]:
    if existing := _find_server_name(
        list(orchestrator.config.mcp_servers), server.name
    ):
        if isinstance(existing, MCPStdio) and existing == server:
            return PersistedMCPServerResult(server=existing, created=False)
        raise MCPServerAddError(
            f"MCP server name `{server.name}` is already configured."
        )
    await _append_persisted_mcp_server(orchestrator, server)
    return PersistedMCPServerResult(server=server, created=True)


async def _append_persisted_mcp_server(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], server: MCPServer
) -> None:
    raw_servers = await _load_persisted_servers(orchestrator, MCPServerAddError)
    failures = await orchestrator.set_field(
        "/mcp_servers",
        [*raw_servers, _serialize_mcp_server(server)],
        reason=f"Add MCP server {server.name}",
    )
    _raise_persistence_failure(
        failures, MCPServerAddError, "Failed to persist MCP server"
    )


async def remove_mcp_server(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], name: str
) -> RemovedMCPServerResult:
    normalized_name = normalize_mcp_server_name(name)
    if not normalized_name:
        raise MCPServerRemoveError("MCP server name must contain letters or numbers.")

    raw_servers = await _load_persisted_servers(orchestrator, MCPServerRemoveError)
    persisted = _find_raw_server(raw_servers, normalized_name)
    if persisted is None:
        return RemovedMCPServerResult(name=normalized_name, server=None, removed=False)
    try:
        removed_server = _parse_raw_mcp_server(persisted)
    except ValidationError as exc:
        raise MCPServerRemoveError(
            "MCP server in the persistence layer is invalid."
        ) from exc

    failures = await orchestrator.set_field(
        "/mcp_servers",
        [
            server
            for server in raw_servers
            if _raw_server_name(server) != normalized_name
        ],
        reason=f"Remove MCP server {normalized_name}",
    )
    _raise_persistence_failure(
        failures, MCPServerRemoveError, "Failed to remove MCP server"
    )
    return RemovedMCPServerResult(
        name=normalized_name, server=removed_server, removed=True
    )


async def find_persisted_oauth_mcp_server(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], name: str
) -> MCPHttp | MCPStreamableHttp | None:
    normalized_name = normalize_mcp_server_name(name)
    if not normalized_name:
        return None
    raw_servers = await _load_persisted_servers(orchestrator, MCPServerRemoveError)
    persisted = _find_raw_server(raw_servers, normalized_name)
    if persisted is None:
        return None
    try:
        server = _parse_raw_mcp_server(persisted)
    except ValidationError:
        return None
    if isinstance(server, MCPHttp | MCPStreamableHttp) and isinstance(
        server.auth, MCPOAuth
    ):
        return server
    return None


def _serialize_mcp_server(server: MCPServer) -> dict[str, object]:
    data = server.model_dump(mode="json", exclude_none=True, exclude_defaults=True)
    if isinstance(server, MCPHttp | MCPStreamableHttp):
        # exclude_defaults drops MCPStaticAuth's `type="static"`, but the persisted
        # auth union is discriminated by `type`, so re-add it.
        data["auth"] = {**data.get("auth", {}), "type": server.auth.type}
    return data


def normalize_mcp_server_url(value: str) -> str:
    parsed = _parse_mcp_server_url(value)
    return _url_with_normalized_host(parsed, trim_trailing_slash=False)


async def _load_persisted_servers(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    error_type: type[MCPServerAddError] | type[MCPServerRemoveError],
) -> list[dict[str, object]]:
    raw = (
        (await orchestrator.load_persistence_layer())
        .model_dump()
        .get("mcp_servers", [])
    )
    if not isinstance(raw, list):
        raise error_type("Config field `mcp_servers` is not a list.")
    return [server for server in raw if isinstance(server, dict)]


def _raise_persistence_failure(
    failures: list[BaseException],
    error_type: type[MCPServerAddError] | type[MCPServerRemoveError],
    message: str,
) -> None:
    if not failures:
        return
    failure = failures[0]
    if isinstance(failure, ConcurrencyConflictError):
        raise failure
    raise error_type(f"{message}: {failure}") from failure


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


def _parse_mcp_server_url(value: str) -> SplitResult:
    raw_url = value.strip()
    if not raw_url:
        raise MCPServerAddError("MCP server URL is required.")
    try:
        parsed = urlsplit(raw_url)
        host = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise MCPServerAddError("MCP server URL must be a valid HTTP(S) URL.") from exc
    scheme = parsed.scheme.lower()
    if not scheme:
        raise MCPServerAddError("MCP server URL must include a scheme.")
    if scheme not in {"http", "https"}:
        raise MCPServerAddError("MCP server URL must use https.")
    if not host:
        raise MCPServerAddError("MCP server URL must include a host.")
    if parsed.fragment:
        raise MCPServerAddError("MCP server URL must not include a fragment.")
    if parsed.username is not None or parsed.password is not None:
        raise MCPServerAddError("MCP server URL must not include credentials.")
    if scheme == "http" and not _is_loopback_host(host):
        raise MCPServerAddError(
            "MCP server URL must use https unless it points to localhost."
        )
    return parsed


def _find_server_name(servers: list[MCPServer], name: str) -> MCPServer | None:
    return next((server for server in servers if server.name == name), None)


def _raw_server_name(raw_server: object) -> str | None:
    if not isinstance(raw_server, dict):
        return None
    name = raw_server.get("name")
    return normalize_mcp_server_name(name) if isinstance(name, str) else None


def _find_raw_server(raw_servers: object, name: str) -> object | None:
    if not isinstance(raw_servers, list):
        return None
    return next(
        (server for server in raw_servers if _raw_server_name(server) == name), None
    )


def _parse_raw_mcp_server(raw_server: object) -> MCPServer:
    return _MCP_SERVER_ADAPTER.validate_python(raw_server)


def _find_server_url(
    servers: list[MCPServer], url: str
) -> MCPHttp | MCPStreamableHttp | None:
    url_key = _url_key(url)
    return next(
        (
            server
            for server in servers
            if isinstance(server, MCPHttp | MCPStreamableHttp)
            and _url_key(server.url) == url_key
        ),
        None,
    )


def _remote_servers_equivalent(
    existing: MCPHttp | MCPStreamableHttp, requested: MCPHttp | MCPStreamableHttp
) -> bool:
    if _url_key(existing.url) != _url_key(requested.url):
        return False
    return existing.model_copy(update={"url": requested.url}) == requested


def _url_key(value: str) -> str:
    return _url_with_normalized_host(urlsplit(value.strip()), trim_trailing_slash=True)


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
    path = parsed.path.rstrip("/") if trim_trailing_slash else parsed.path
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
    labels = [label for label in (parsed.hostname or "mcp").lower().split(".") if label]
    if (
        len(labels) >= _LEADING_HOST_PREFIX_LABEL_MIN_COUNT
        and labels[0] in _HOST_PREFIXES_TO_DROP
    ):
        labels = labels[1:]
    candidate = labels[0] if labels else ""
    if candidate in _GENERIC_ALIAS_SEGMENTS:
        candidate = _path_alias_candidate(parsed.path)
    return normalize_mcp_server_name(candidate) or "mcp"


def _path_alias_candidate(path: str) -> str:
    for segment in path.split("/"):
        normalized = normalize_mcp_server_name(segment.lower())
        if normalized and normalized not in _GENERIC_ALIAS_SEGMENTS:
            return normalized
    return ""


def _dedupe_server_name(base: str, existing_names: set[str]) -> str:
    if base not in existing_names:
        return base
    index = 2
    while f"{base}_{index}" in existing_names:
        index += 1
    return f"{base}_{index}"


__all__ = [
    "MCPServerAddError",
    "MCPServerRemoveError",
    "PersistedMCPServerResult",
    "RemovedMCPServerResult",
    "find_persisted_oauth_mcp_server",
    "normalize_mcp_server_url",
    "persist_oauth_mcp_server",
    "persist_remote_mcp_server",
    "persist_stdio_mcp_server",
    "remove_mcp_server",
]
