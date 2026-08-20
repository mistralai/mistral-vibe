"""Unit tests for VisualStreamingOutput in vibe/cli/rich_streaming.py."""
from __future__ import annotations

from unittest.mock import MagicMock

from vibe.app_server.models import PublicEffectEntry
from vibe.cli.rich_streaming import _format_tool_title
from vibe.utils.tool_presentation import EffectCallDisplay, ToolEffectKind


def test_format_tool_title_preserves_full_length_paths():
    """Verify that long file paths are not truncated with 70-character ellipsis."""
    long_path = "/Users/i/src/nan.web/apps/3rdparty/eaukraine.eu/web/src/domain/LongDeeplyNestedPathFile.js"
    detail = MagicMock()
    detail.kind = ToolEffectKind.FILE_READ
    detail.display = EffectCallDisplay(
        summary=f"Reading {long_path}",
        verb="Reading",
        message=long_path,
        status_text="Reading file",
    )
    detail.tool_name = "read_file"

    entry = MagicMock(spec=PublicEffectEntry)
    entry.detail = detail

    icon, label = _format_tool_title(entry)
    assert icon == "👀"
    assert long_path in label
    assert "..." not in label
