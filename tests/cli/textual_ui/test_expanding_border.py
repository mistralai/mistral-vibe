from __future__ import annotations

from unittest.mock import patch

from textual.geometry import Size

from vibe.cli.textual_ui.widgets.messages import ExpandingBorder


def test_get_content_width_is_constant_without_rendering() -> None:
    border = ExpandingBorder()

    with patch.object(ExpandingBorder, "render") as mock_render:
        width = border.get_content_width(Size(80, 24), Size(80, 24))

    assert width == 1
    mock_render.assert_not_called()
