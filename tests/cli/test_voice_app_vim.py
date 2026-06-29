from __future__ import annotations

import pytest
from textual import events

from vibe.cli.textual_ui.widgets.voice_app import VoiceApp
from vibe.core.config import VibeConfig


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> VoiceApp:
    widget = VoiceApp(VibeConfig())
    monkeypatch.setattr(widget, "_update_display", lambda: None)
    return widget


class TestVoiceAppVimKeybindings:
    def test_j_moves_down(self, app: VoiceApp):
        assert app.selected_index == 0

        app.on_key(events.Key("j", "j"))

        assert app.selected_index == 1

    def test_k_wraps_to_last(self, app: VoiceApp):
        assert app.selected_index == 0

        app.on_key(events.Key("k", "k"))

        assert app.selected_index == len(app.settings) - 1
