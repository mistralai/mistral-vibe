"""Plugin catalogue builders for scenarios."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

METHODS = frozenset({"plugin_catalog/read", "plugin/reload"})


def entry(
    name: str,
    digest: str,
    *,
    version: str | None = "1.0.0",
    description: str = "",
    scope: str | None = "global",
    installed: bool = True,
    components: Sequence[tuple[str, str]] = (),
    drifted: int = 0,
) -> dict[str, Any]:
    """One plugin the session pinned; `components` are `(kind, name)` pairs."""
    return {
        "name": name,
        "version": version,
        "sourceFormat": "agent_plugins_1_0",
        "manifestDigest": f"manifest-{name}",
        "description": description,
        "author": "Mistral AI",
        "scope": scope,
        "contentSha256": digest,
        "pinnedRoot": f"/opt/vibe/plugins/{name}",
        "installedRoot": f"/opt/vibe/plugins/{name}" if installed else None,
        "components": [
            {"kind": kind, "name": component} for kind, component in components
        ],
        "drifted": drifted,
    }


def dropped(file: str, message: str) -> dict[str, str]:
    """One plugin file the resolve could not load."""
    return {"file": file, "message": message}


def catalog(
    plugins: Sequence[Mapping[str, Any]], dropped: Sequence[Mapping[str, str]] = ()
) -> dict[str, Any]:
    """A `plugin_catalog/read` answer."""
    return {"plugins": {"plugins": list(plugins), "dropped": list(dropped)}}


def sample_plugins() -> list[dict[str, Any]]:
    """Plugins covering components, drift, an unknown scope and an uninstall since pin."""
    return [
        entry(
            "devtools",
            "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
            description="Lint, format and review helpers for everyday development.",
            components=[
                ("skill", "lint"),
                ("skill", "format"),
                ("mcp_server", "filesystem"),
                ("hook", "pre-commit"),
            ],
            drifted=1,
        ),
        entry("docs", "a1b2c3d4e5f60718293a4b5c6d7e8f90", scope=None, version=None),
        entry("legacy", "ffeeddccbbaa00998877665544332211", installed=False),
    ]
