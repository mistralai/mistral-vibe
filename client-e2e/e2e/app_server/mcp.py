"""MCP handshake builders for scenarios."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def tool(name: str, description: str = "", *, enabled: bool = True) -> dict[str, Any]:
    """One discovered MCP tool."""
    return {"name": name, "description": description, "enabled": enabled}


def server(
    name: str,
    *,
    transport: str = "stdio",
    status: str = "connected",
    tools: Sequence[Mapping[str, Any]] = (),
    error: str | None = None,
) -> dict[str, Any]:
    """One locally configured MCP server."""
    return _source(name, "server", transport, status, tools, error)


def connector(
    name: str,
    *,
    transport: str = "connector",
    status: str = "connected",
    tools: Sequence[Mapping[str, Any]] = (),
    error: str | None = None,
) -> dict[str, Any]:
    """One workspace connector."""
    return _source(name, "connector", transport, status, tools, error)


def sample_sources() -> list[dict[str, Any]]:
    """Local servers and workspace connectors covering every status column."""
    return [
        server(
            "filesystem",
            tools=[
                tool("read_file", "Read a file from disk"),
                tool("write_file", "Write a file to disk", enabled=False),
            ],
        ),
        server("weather", transport="http", status="needs_auth"),
        server("broken", status="unavailable"),
        connector("github", tools=[tool("create_issue", "Open a GitHub issue")]),
        connector("gmail", status="disabled"),
    ]


SAMPLE_DISCOVERY_ERRORS = {"broken": "spawn failed: command not found"}


def handshake(
    sources: Sequence[Mapping[str, Any]],
    *,
    discovery_errors: Mapping[str, str] | None = None,
    connector_error: str | None = None,
    toggled: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Publish `sources` as the MCP state; `toggled` is what `mcp/toggle` answers."""
    state = _state(sources, discovery_errors, connector_error)
    overrides: dict[str, Any] = {"runtime/read": {"runtime": {"mcp": state}}}
    if toggled is not None:
        overrides["mcp/toggle"] = {
            "runtime": {"mcp": _state(toggled, discovery_errors, connector_error)}
        }
    return overrides


def _source(
    name: str,
    kind: str,
    transport: str,
    status: str,
    tools: Sequence[Mapping[str, Any]],
    error: str | None,
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "transport": transport,
        "status": status,
        "tools": list(tools),
        "error": error,
    }


def _state(
    sources: Sequence[Mapping[str, Any]],
    discovery_errors: Mapping[str, str] | None,
    connector_error: str | None,
) -> dict[str, Any]:
    return {
        "sources": list(sources),
        "discoveryErrors": dict(discovery_errors or {}),
        "connectorError": connector_error,
    }
