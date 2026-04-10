from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from vibe.core.config import MCPServer, MCPStdio
from vibe.core.tools.mcp_sampling import MCPSamplingHandler


class MCPClient:
    """MCP client for remote subagent execution with multi-transport support."""

    def __init__(self, server_config: MCPServer):
        self.server_config = server_config
        # Only set URL and headers for HTTP transports
        if not self._is_stdio_transport():
            self.url = self._get_server_url()
            self.headers = self._get_server_headers()
        else:
            self.url = ""  # Not used for STDIO
            self.headers = {}

    def _get_server_url(self) -> str:
        """Extract URL from MCP server configuration."""
        if hasattr(self.server_config, 'url'):
            return self.server_config.url
        else:
            raise ValueError("MCP server configuration must have a URL for HTTP transport")

    def _get_server_headers(self) -> dict[str, str]:
        """Get HTTP headers from MCP server configuration."""
        if hasattr(self.server_config, 'http_headers'):
            return self.server_config.http_headers()
        return {}

    def _is_stdio_transport(self) -> bool:
        """Check if this server uses STDIO transport."""
        return isinstance(self.server_config, MCPStdio)

    def _get_stdio_parameters(self) -> StdioServerParameters:
        """Get STDIO parameters for subprocess execution."""
        if not self._is_stdio_transport():
            raise ValueError("Server is not configured for STDIO transport")

        config = self.server_config
        command = config.command if isinstance(config.command, list) else [config.command]
        args = getattr(config, 'args', [])
        env = getattr(config, 'env', {})

        return StdioServerParameters(
            command=command[0],
            args=[*command[1:], *args],
            env=env
        )

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        sampling_callback: MCPSamplingHandler | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Call a tool on the remote MCP server and stream events."""
        if self._is_stdio_transport():
            # Use STDIO transport for subprocess communication
            params = self._get_stdio_parameters()
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, sampling_callback=sampling_callback) as session:
                    await session.initialize()

                    # Call the tool and stream results
                    async for chunk in await session.call_tool_streaming(tool_name, arguments):
                        if chunk.type == "stream":
                            yield chunk.data
                        elif chunk.type == "result":
                            # Final result - we'll handle this as part of the stream
                            yield chunk.data
                        elif chunk.type == "error":
                            yield {"type": "error", "message": chunk.message}
        else:
            # Use HTTP transport for remote communication
            async with streamablehttp_client(self.url, headers=self.headers) as (read, write, _):
                async with ClientSession(read, write, sampling_callback=sampling_callback) as session:
                    await session.initialize()

                    # Call the tool and stream results
                    async for chunk in await session.call_tool_streaming(tool_name, arguments):
                        if chunk.type == "stream":
                            yield chunk.data
                        elif chunk.type == "result":
                            # Final result - we'll handle this as part of the stream
                            yield chunk.data
                        elif chunk.type == "error":
                            yield {"type": "error", "message": chunk.message}