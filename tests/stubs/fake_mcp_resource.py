from __future__ import annotations

from typing import Literal

from vibe.app_server.models import MCPState


class FakeMCPResource:
    """Stands in for the MCPResource on an ACP session's app-server resources."""

    def __init__(
        self,
        state: MCPState | None = None,
        *,
        auth_url: str | None = None,
        tool_count: int = 0,
        mutated_state: MCPState | None = None,
    ) -> None:
        self.state = state if state is not None else MCPState()
        self.auth_url = auth_url
        self.tool_count = tool_count
        # What the real resource recomputes and applies on refresh/toggle.
        self._mutated_state = mutated_state
        self.read_calls = 0
        self.auth_url_calls: list[str] = []
        self.refresh_calls: list[str] = []
        self.toggle_calls: list[tuple[str, str, bool, str | None]] = []

    async def read(self) -> MCPState:
        self.read_calls += 1
        return self.state

    async def connector_auth_url(self, name: str) -> str | None:
        self.auth_url_calls.append(name)
        return self.auth_url

    async def refresh_connector(self, name: str) -> int:
        self.refresh_calls.append(name)
        self._apply_mutation()
        return self.tool_count

    async def toggle(
        self,
        name: str,
        *,
        source: Literal["server", "connector"],
        disabled: bool,
        tool_name: str | None = None,
    ) -> MCPState:
        self.toggle_calls.append((name, source, disabled, tool_name))
        return self._apply_mutation()

    def _apply_mutation(self) -> MCPState:
        if self._mutated_state is not None:
            self.state = self._mutated_state
        return self.state
