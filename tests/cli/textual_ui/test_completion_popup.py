from __future__ import annotations

from vibe.cli.textual_ui.widgets.chat_input.completion_popup import CompletionPopup


def test_rendered_text_length_uses_terminal_cell_width() -> None:
    # "你" and "🙂" both occupy 2 terminal cells in Rich (+2 for separator).
    assert CompletionPopup.rendered_text_length("@你", "🙂") == 6


def test_rendered_text_length_keeps_description_separator() -> None:
    assert CompletionPopup.rendered_text_length("@abc", "def") == 8
