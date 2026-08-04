from __future__ import annotations

import pytest
from textual import events

from vibe.cli.textual_ui.widgets.rewind_app import RewindApp


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> RewindApp:
    widget = RewindApp("preview", has_file_changes=True)
    monkeypatch.setattr(widget, "_update_options", lambda: None)
    return widget


class TestRewindAppVimKeybindings:
    def test_action_help_describes_escape_as_previous(self, app: RewindApp):
        assert "←/Esc previous" in app._help_text().plain

    def test_persistence_help_describes_q_as_quit(self, app: RewindApp):
        app._advance_to_persistence(restore_files=False)

        assert "q quit" in app._help_text().plain

    def test_j_moves_down(self, app: RewindApp):
        assert app.selected_option == 0

        app.on_key(events.Key("j", "j"))

        assert app.selected_option == 1

    def test_k_wraps_to_last(self, app: RewindApp):
        assert app.selected_option == 0

        app.on_key(events.Key("k", "k"))

        assert app.selected_option == len(app._options) - 1
