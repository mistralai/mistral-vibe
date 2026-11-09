"""The launch context a connected client's experiment eval is evaluated with.

Kept out of the protocol models so importing them does not load telemetry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibe import __version__
from vibe.core.telemetry.build_metadata import build_launch_context

if TYPE_CHECKING:
    from vibe.app_server._connection_protocol import ClientInfo
    from vibe.core.telemetry.types import LaunchContext


def client_launch_context(client_info: ClientInfo) -> LaunchContext:
    return build_launch_context(
        agent_entrypoint=client_info.entrypoint,
        agent_version=__version__,
        client_name=client_info.name,
        client_version=client_info.version,
        terminal_emulator=client_info.terminal_emulator,
    )


__all__ = ["client_launch_context"]
