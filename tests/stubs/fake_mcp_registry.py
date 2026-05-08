from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.tools.mcp import MCPRegistry
from vibe.core.tools.mcp.tools import (
    RemoteTool,
    create_mcp_http_proxy_tool_class,
    create_mcp_stdio_proxy_tool_class,
)

if TYPE_CHECKING:
    from vibe.core.config import MCPServer
    from vibe.core.tools.base import BaseTool


_BROKEN_SERVER_NAME = "broken-server"


class FakeMCPRegistry(MCPRegistry):
    async def get_tools_async(
        self, servers: list[MCPServer]
    ) -> dict[str, type[BaseTool]]:
        return self.get_tools(servers)

    def set_tools(
        self, servers: list[MCPServer], tools: dict[str, type[BaseTool]]
    ) -> None:
        for srv in servers:
            key = self._server_key(srv)
            self._cache[key] = dict(tools)

    def get_tools(self, servers: list[MCPServer]) -> dict[str, type[BaseTool]]:
        result: dict[str, type[BaseTool]] = {}
        for srv in servers:
            key = self._server_key(srv)
            if key not in self._cache:
                remote = RemoteTool(
                    name="fake_tool", description=f"A fake tool for {srv.name}"
                )
                match srv.transport:
                    case "stdio":
                        proxy = create_mcp_stdio_proxy_tool_class(
                            command=["fake-cmd"], remote=remote, alias=srv.name
                        )
                    case "http" | "streamable-http":
                        proxy = create_mcp_http_proxy_tool_class(
                            url="http://fake-mcp-server", remote=remote, alias=srv.name
                        )
                    case _:
                        raise ValueError(
                            f"FakeMCPRegistry: unsupported transport {srv.transport!r}"
                        )
                self._cache[key] = {proxy.get_name(): proxy}
            result.update(self._cache[key])
        return result


class FakeMCPRegistryWithBrokenServer(FakeMCPRegistry):
    def get_tools(self, servers: list[MCPServer]) -> dict[str, type[BaseTool]]:
        working = [s for s in servers if s.name != _BROKEN_SERVER_NAME]
        return super().get_tools(working)
