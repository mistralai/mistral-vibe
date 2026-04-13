"""Test that _LogView.render_line handles width mismatches during resize."""

from __future__ import annotations

from unittest.mock import PropertyMock, patch

import pytest
from textual.app import App, ComposeResult
from textual.geometry import Size
from textual.strip import Strip

from vibe.cli.textual_ui.widgets.debug_console import _LogView


class _LogViewTestApp(App):
    def compose(self) -> ComposeResult:
        self._log_view = _LogView(
            load_page=lambda: None, has_more=lambda: False, id="test-log-view"
        )
        yield self._log_view


@pytest.mark.asyncio
async def test_render_line_no_keyerror_on_width_mismatch():
    """render_line must not raise KeyError when wrap width != cached width."""
    app = _LogViewTestApp()
    async with app.run_test(size=(80, 24)) as pilot:
        log_view = app._log_view

        long_line = "A" * 200
        log_view.write_line(long_line)
        await pilot.pause()

        assert log_view._total_visual == 3
        assert log_view._cached_width == 80

        # At width 120, wrapping produces 2 lines, but _wrap_prefix says 3.
        new_size = Size(120, 24)
        log_view._render_line_cache.clear()
        with patch.object(
            type(log_view), "size", new_callable=PropertyMock, return_value=new_size
        ):
            result = log_view.render_line(2)
            assert isinstance(result, Strip)
