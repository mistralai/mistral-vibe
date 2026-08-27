from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vibe.app_server.client_tools import ClientToolHandler
    from vibe.app_server.host import AppServerHost
    from vibe.app_server.session import AppServerSession, SessionExitSummary

__all__ = [
    "AppServerHost",
    "AppServerSession",
    "ClientToolHandler",
    "SessionExitSummary",
]

_MAPPING: dict[str, tuple[str, str]] = {
    "AppServerHost": ("vibe.app_server.host", "AppServerHost"),
    "AppServerSession": ("vibe.app_server.session", "AppServerSession"),
    "ClientToolHandler": ("vibe.app_server.client_tools", "ClientToolHandler"),
    "SessionExitSummary": ("vibe.app_server.session", "SessionExitSummary"),
}


def __getattr__(name: str) -> object:
    if name in _MAPPING:
        import importlib

        module_name, attr_name = _MAPPING[name]
        module = importlib.import_module(module_name)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
