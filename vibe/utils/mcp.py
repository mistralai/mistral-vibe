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


def format_tool_display_description(
    description: str | None, *, source_name: str = ""
) -> str:
    """Reduce a remote tool description to the single line the UI can show.

    Remote MCP and connector descriptions are free-form and routinely span
    several lines. The `/mcp` detail view renders one non-wrapping row per
    tool, so anything past the first line breaks the layout.

    The legacy backend flattens MCP server tools because it derives them from
    prompt-facing tool classes, whose descriptions carry a `[source] ` prefix
    and a trailing hint. The Unified Harness reports remote descriptors
    verbatim, so it has to flatten explicitly to match. `source_name` is the
    alias to strip; pass `""` to only take the first line.
    """
    if not description:
        return ""
    head = description.removeprefix(f"[{source_name}] ") if source_name else description
    return head.split("\n", 1)[0].strip()


__all__ = [
    "MCPAddTransport",
    "format_tool_display_description",
    "parse_mcp_add_transport",
]
