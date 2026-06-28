from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
)
from tests.stubs.fake_voice_manager import FakeVoiceManager
from vibe.cli.narrator_manager.narrator_manager_port import (
    NarratorManagerListener,
    NarratorState,
)
from vibe.cli.textual_ui.lazy_audio_managers import (
    LazyNarratorManager,
    LazyVoiceManager,
)
from vibe.cli.voice_manager.voice_manager_port import TranscribeState
from vibe.core.config import VibeConfig
from vibe.core.types import BaseEvent


class FakeNarratorManager:
    state = NarratorState.IDLE
    is_playing = False

    def __init__(self) -> None:
        self.listeners: list[NarratorManagerListener] = []
        self.synced = False

    def on_turn_start(self, user_message: str) -> None:
        pass

    def on_turn_event(self, event: BaseEvent) -> None:
        pass

    def on_turn_error(self, message: str) -> None:
        pass

    def on_turn_cancel(self) -> None:
        pass

    def on_turn_end(self) -> None:
        pass

    def cancel(self) -> None:
        pass

    def sync(self) -> None:
        self.synced = True

    def add_listener(self, listener: NarratorManagerListener) -> None:
        if listener not in self.listeners:
            self.listeners.append(listener)

    def remove_listener(self, listener: NarratorManagerListener) -> None:
        try:
            self.listeners.remove(listener)
        except ValueError:
            pass

    async def close(self) -> None:
        pass


def test_importing_tui_app_does_not_import_optional_audio_modules() -> None:
    code = """
import sys
import vibe.cli.textual_ui.app

blocked = [
    "sounddevice",
    "vibe.cli.narrator_manager.narrator_manager",
    "vibe.cli.voice_manager.voice_manager",
    "vibe.core.audio_player.audio_player",
    "vibe.core.audio_recorder.audio_recorder",
    "vibe.core.transcribe.factory",
    "vibe.core.tts.factory",
]
loaded = [name for name in blocked if name in sys.modules]
if loaded:
    raise SystemExit(f"unexpected optional modules loaded: {loaded}")
"""

    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_default_tui_app_does_not_materialize_disabled_optional_managers() -> None:
    config = build_test_vibe_config(voice_mode_enabled=False, narrator_enabled=False)

    with (
        patch(
            "vibe.cli.textual_ui.lazy_audio_managers._create_real_voice_manager",
            side_effect=AssertionError("voice should stay lazy"),
        ),
        patch(
            "vibe.cli.textual_ui.lazy_audio_managers._create_real_narrator_manager",
            side_effect=AssertionError("narrator should stay lazy"),
        ),
    ):
        app = build_test_vibe_app(config=config, voice_manager=None)

    assert app._voice_manager.is_enabled is False
    assert app._voice_manager.transcribe_state == TranscribeState.IDLE
    assert app._narrator_manager.state == NarratorState.IDLE


def test_lazy_voice_manager_materializes_when_used() -> None:
    config = build_test_vibe_config(voice_mode_enabled=False)
    factory = MagicMock(return_value=FakeVoiceManager(is_voice_ready=True))
    manager = LazyVoiceManager(lambda: config, factory)

    assert manager.is_enabled is False
    assert manager.transcribe_state == TranscribeState.IDLE
    factory.assert_not_called()

    manager.start_recording()

    factory.assert_called_once()
    assert manager.transcribe_state == TranscribeState.RECORDING


def test_lazy_narrator_manager_materializes_when_enabled_at_startup() -> None:
    config = build_test_vibe_config(narrator_enabled=True)
    narrator = FakeNarratorManager()
    factory = MagicMock(return_value=narrator)

    manager = LazyNarratorManager(lambda: config, factory)

    factory.assert_called_once()
    assert manager.state == NarratorState.IDLE


def test_lazy_narrator_manager_sync_materializes_after_enable() -> None:
    config = build_test_vibe_config(narrator_enabled=False)
    narrator = FakeNarratorManager()
    factory = MagicMock(return_value=narrator)
    manager = LazyNarratorManager(lambda: config, factory)

    config.narrator_enabled = True
    manager.sync()

    factory.assert_called_once()
    assert narrator.synced is False


def test_tui_config_refresh_syncs_lazy_narrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial_config = build_test_vibe_config(narrator_enabled=False)
    refreshed_config = build_test_vibe_config(narrator_enabled=True)
    agent_loop = build_test_agent_loop(config=initial_config)
    narrator = FakeNarratorManager()
    factory = MagicMock(return_value=narrator)
    narrator_manager = LazyNarratorManager(lambda: agent_loop.config, factory)
    app = build_test_vibe_app(agent_loop=agent_loop, narrator_manager=narrator_manager)
    monkeypatch.setattr(VibeConfig, "load", staticmethod(lambda: refreshed_config))

    app._refresh_config_from_disk()

    factory.assert_called_once()
    assert narrator.synced is False
