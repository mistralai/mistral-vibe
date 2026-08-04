from __future__ import annotations

from typing import Literal

type MCPAddTransport = Literal["http", "streamable-http"]


def parse_mcp_add_transport(value: str) -> MCPAddTransport:
    match value:
        case "http" | "streamable-http":
            return value
        case _:
            raise ValueError(
                "MCP server transport must be one of: http, streamable-http."
            )


__all__ = ["MCPAddTransport", "parse_mcp_add_transport"]
