"""MCP server discoveries, shared by every session of the OS process.

Sessions that would contact a server at the same time with the same credential
share one request, and a server that failed is not contacted again under that
credential until its backoff ends: an unreachable server is asked once, not
once per session that opens. Each session keeps its own copy of the
descriptors over the on-disk cache, which is what sessions and processes
share.

The descriptors are not a ``RemoteCache``: they are fetched over each
session's own connections and credential (a stdio server runs in the session's
pool), so the flights share the request between sessions rather than making it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe import MCPDiscoveryFlights

_RETRY_AFTER_S = 30
_MAX_RETRY_AFTER_S = 10 * 60


class MCPDiscoveryService:
    def __init__(self) -> None:
        self._flights: MCPDiscoveryFlights | None = None

    # Built on first use: importing the Harness would slow every process start.
    def flights(self) -> MCPDiscoveryFlights:
        if self._flights is None:
            from mistralai_vibe_local_harness.vibe import MCPDiscoveryFlights

            self._flights = MCPDiscoveryFlights(
                retry_after_s=_RETRY_AFTER_S, max_retry_after_s=_MAX_RETRY_AFTER_S
            )
        return self._flights

    async def close(self) -> None:
        flights, self._flights = self._flights, None
        if flights is not None:
            await flights.aclose()


__all__ = ["MCPDiscoveryService"]
