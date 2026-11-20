"""A stdio MCP server for the Vibebot local tools tests: read-only and write
tools, a large answer and a slow one.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("fake")


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def echo(text: str) -> str:
    """Echo the text back."""
    return f"echo: {text}"


@server.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
def write_note(text: str) -> str:
    """Write a note (pretend)."""
    return f"wrote: {text}"


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def big(size: int) -> str:
    """Return size bytes of text."""
    return "x" * size


@server.tool(annotations=ToolAnnotations(read_only_hint=True))
def slow(seconds: float) -> str:
    """Sleep then answer."""
    import time

    time.sleep(seconds)
    return "done"


if __name__ == "__main__":
    server.run("stdio")
