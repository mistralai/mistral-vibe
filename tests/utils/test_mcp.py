from __future__ import annotations

import pytest

from vibe.utils.mcp import format_tool_display_description


@pytest.mark.parametrize(
    ("description", "source_name", "expected"),
    [
        (None, "notion", ""),
        ("", "notion", ""),
        ("Search pages.", "notion", "Search pages."),
        ("[notion] Search pages.", "notion", "Search pages."),
        ("[other] Search pages.", "notion", "[other] Search pages."),
        ("[notion] Search pages.", "", "[notion] Search pages."),
        ("Search pages.\nHint: use sparingly.", "notion", "Search pages."),
        ("[notion] Search pages.\r\nHint: use sparingly.", "notion", "Search pages."),
        ("  Search pages.  \n\nMore.", "notion", "Search pages."),
        ("\n\nSearch pages.", "notion", ""),
    ],
)
def test_format_tool_display_description(
    description: str | None, source_name: str, expected: str
) -> None:
    assert (
        format_tool_display_description(description, source_name=source_name)
        == expected
    )


def test_format_tool_display_description_is_idempotent() -> None:
    once = format_tool_display_description(
        "[notion] Search pages.\nHint: use sparingly.", source_name="notion"
    )
    assert format_tool_display_description(once, source_name="notion") == once
