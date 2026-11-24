"""The parts of a plugin that Vibe does not support yet (DSK-2284).

Vibe Work runs a plugin's Views, the MCP Apps of its own MCP servers and its
volumes; Vibe does not yet. (Its starter prompt is supported: the Plugins page's
"Try in chat" starts with it, DSK-2397.) The plugin still loads with its other
parts. These are only read from the plugin's files, so the Plugins
page can say which parts are not supported, and the model does not call a tool
that does not exist (``render_<view>``) or promise a UI the user will not see.

Reading never fails: a part whose file is missing or malformed is not reported.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from vibe.core.plugins.views import plugin_views

if TYPE_CHECKING:
    from vibe.core.plugins._native import PluginDescriptor

logger = logging.getLogger(__name__)

type UnsupportedPart = Literal["view", "mcp_app", "volumes"]

_MCP_MANIFEST = Path("ai.mistral.vibe") / "mcp-manifest.json"
_UI_URI_SCHEME = "ui://"
# A built manifest can inline its apps' HTML. Past this size it is not parsed,
# to keep listings and session starts fast: a ``ui://`` mention is taken as an
# app, which it almost always is. A trade-off, on purpose.
_MAX_PARSED_MCP_MANIFEST_BYTES = 4 * 1024 * 1024
_SCAN_CHUNK_BYTES = 1024 * 1024


def unsupported_parts(plugin: PluginDescriptor) -> list[UnsupportedPart]:
    """The parts the plugin ships that Vibe does not support yet.

    Never raises: a plugin's files are not trusted (a synced plugin is someone
    else's), and this runs at every session start and Plugins page listing.
    """
    try:
        return _parts(plugin, has_views=bool(plugin_views(plugin.root)))
    except Exception:
        logger.warning(
            "Could not read the parts of plugin %r", plugin.name, exc_info=True
        )
        return []


def unsupported_note(plugin: PluginDescriptor) -> str | None:
    """What the model is told of those parts, or None when there are none."""
    try:
        views = [name for name, _ in plugin_views(plugin.root)]
        parts = _parts(plugin, has_views=bool(views))
    except Exception:
        logger.warning(
            "Could not read the parts of plugin %r", plugin.name, exc_info=True
        )
        return None
    if not parts:
        return None
    sentences: list[str] = []
    if "view" in parts:
        listed = ", ".join(f"`{name}`" for name in views)
        sentences.append(
            f"its Views ({listed}): the `render_*` tools that show them do not "
            "exist here, so do not call them"
        )
    if "mcp_app" in parts:
        sentences.append(
            "the interactive UI (MCP Apps) of its MCP server's tools: the tools "
            "run, so give the user their result as text"
        )
    if "volumes" in parts:
        sentences.append("its volumes, which are not mounted")
    return (
        "Not supported in this app for now: "
        + "; ".join(sentences)
        + ". If the user asks for these parts, tell them they are not supported "
        "here for now."
    )


def _parts(plugin: PluginDescriptor, *, has_views: bool) -> list[UnsupportedPart]:
    found: dict[UnsupportedPart, bool] = {
        "view": has_views,
        "mcp_app": _has_mcp_app(plugin.root),
        "volumes": plugin.has_volumes,
    }
    return [part for part, present in found.items() if present]


def _has_mcp_app(root: Path) -> bool:
    """Whether the build-time MCP manifest declares a ``ui://`` app.

    The manifest is what Vibe Work and AI Registry read: a UI resource, or a
    tool whose ``_meta`` points at one.
    """
    path = root / _MCP_MANIFEST
    try:
        if path.stat().st_size > _MAX_PARSED_MCP_MANIFEST_BYTES:
            return _mentions_ui_uri(path)
        raw = path.read_bytes()
    except OSError:
        return False
    if _UI_URI_SCHEME.encode() not in raw:
        return False
    try:
        document = json.loads(raw)
    except (ValueError, RecursionError):
        return False
    servers = document.get("servers") if isinstance(document, dict) else None
    if not isinstance(servers, dict):
        return False
    return any(_server_has_app(server) for server in servers.values())


def _mentions_ui_uri(path: Path) -> bool:
    """Whether a manifest too large to parse mentions ``ui://``, read in chunks.

    Memory stays bounded however large the file is; the scan stops at the
    first mention, which is usually near the top.
    """
    needle = _UI_URI_SCHEME.encode()
    tail = b""
    with path.open("rb") as file:
        while chunk := file.read(_SCAN_CHUNK_BYTES):
            if needle in tail + chunk:
                return True
            tail = chunk[-(len(needle) - 1) :]
    return False


def _server_has_app(server: object) -> bool:
    if not isinstance(server, dict):
        return False
    resources = server.get("uiResources")
    if isinstance(resources, dict) and any(_is_ui_uri(uri) for uri in resources):
        return True
    return any(_tool_has_app(tool) for tool in _list(server.get("tools")))


def _tool_has_app(tool: object) -> bool:
    meta = tool.get("_meta") if isinstance(tool, dict) else None
    if not isinstance(meta, dict):
        return False
    ui = meta.get("ui")
    nested = ui.get("resourceUri") if isinstance(ui, dict) else None
    return _is_ui_uri(nested) or _is_ui_uri(meta.get("ui/resourceUri"))


def _is_ui_uri(value: object) -> bool:
    return (
        isinstance(value, str)
        and value.startswith(_UI_URI_SCHEME)
        and value.strip() != _UI_URI_SCHEME
    )


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []
