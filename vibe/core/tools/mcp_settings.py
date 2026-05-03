"""Persist MCP server and connector enable/disable settings.

Shared by all entrypoints (CLI, ACP, etc.) so toggle logic is not
tied to a particular UI layer.
"""

from __future__ import annotations

from typing import Any

from vibe.core.config import VibeConfig


def updated_tool_list(tools: list[str], name: str, disabled: bool) -> list[str]:
    """Return a new disabled_tools list with *name* added or removed (unique)."""
    if disabled:
        return list(dict.fromkeys([*tools, name]))
    return [t for t in tools if t != name]


def persist_mcp_toggle(
    config: VibeConfig,
    *,
    name: str,
    is_connector: bool,
    disabled: bool,
    tool_name: str | None = None,
) -> None:
    """Save an MCP server/connector or individual tool toggle to the config file."""
    if is_connector:
        _persist_connector_toggle(name=name, disabled=disabled, tool_name=tool_name)
    else:
        _persist_server_toggle(name=name, disabled=disabled, tool_name=tool_name)


def _persist_server_toggle(*, name: str, disabled: bool, tool_name: str | None) -> None:
    persisted = VibeConfig.get_persisted_config()
    servers: list[dict[str, Any]] = list(persisted.get("mcp_servers", []))
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
    VibeConfig.save_updates({"mcp_servers": servers})


def _persist_connector_toggle(
    *, name: str, disabled: bool, tool_name: str | None
) -> None:
    persisted = VibeConfig.get_persisted_config()
    connectors: list[dict[str, Any]] = list(persisted.get("connectors", []))
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
    VibeConfig.save_updates({"connectors": connectors})
