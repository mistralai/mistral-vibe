"""Persist MCP server and connector enable/disable settings."""

from __future__ import annotations

from typing import Any

from vibe.core.config import VibeConfigSchema
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.types import ConcurrencyConflictError


def updated_tool_list(tools: list[str], name: str, disabled: bool) -> list[str]:
    if disabled:
        return list(dict.fromkeys([*tools, name]))
    return [tool for tool in tools if tool != name]


async def persist_mcp_toggle(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    *,
    name: str,
    is_connector: bool,
    disabled: bool,
    tool_name: str | None = None,
) -> None:
    if is_connector:
        await _persist_connector_toggle(
            orchestrator, name=name, disabled=disabled, tool_name=tool_name
        )
        return
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
    servers = await _load_persisted_list(orchestrator, "mcp_servers")
    for server in servers:
        if server.get("name") != name:
            continue
        if tool_name is not None:
            server["disabled_tools"] = updated_tool_list(
                server.get("disabled_tools", []), tool_name, disabled
            )
        else:
            server["disabled"] = disabled
        await _persist_field(orchestrator, "/mcp_servers", servers)
        return


async def _persist_connector_toggle(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    *,
    name: str,
    disabled: bool,
    tool_name: str | None,
) -> None:
    connectors = await _load_persisted_list(orchestrator, "connectors")
    for connector in connectors:
        if connector.get("name") != name:
            continue
        if tool_name is not None:
            connector["disabled_tools"] = updated_tool_list(
                connector.get("disabled_tools", []), tool_name, disabled
            )
        else:
            connector["disabled"] = disabled
        break
    else:
        connector: dict[str, Any] = {"name": name}
        if tool_name is not None:
            connector["disabled_tools"] = [tool_name] if disabled else []
        else:
            connector["disabled"] = disabled
        connectors.append(connector)
    await _persist_field(orchestrator, "/connectors", connectors)


async def _load_persisted_list(
    orchestrator: ConfigOrchestrator[VibeConfigSchema], field: str
) -> list[dict[str, Any]]:
    raw = (await orchestrator.load_persistence_layer()).model_dump().get(field, [])
    if not isinstance(raw, list):
        raise ValueError(f"Cannot update {field}: expected a list.")
    return [item for item in raw if isinstance(item, dict)]


async def _persist_field(
    orchestrator: ConfigOrchestrator[VibeConfigSchema],
    path: str,
    value: list[dict[str, Any]],
) -> None:
    failures = await orchestrator.set_field(path, value)
    if not failures:
        return
    failure = failures[0]
    if isinstance(failure, ConcurrencyConflictError):
        raise failure
    raise ValueError(f"Failed to persist {path}: {failure}") from failure


__all__ = ["persist_mcp_toggle", "updated_tool_list"]
