from __future__ import annotations

import subprocess
import sys
from unittest.mock import MagicMock

from tests.stubs.app_config import build_test_app_config
from tests.stubs.fake_voice_manager import FakeVoiceManager
from vibe.cli.lazy_audio_managers import LazyNarratorManager, LazyVoiceManager
from vibe.cli.narrator_manager.narrator_manager_port import (
    NarratorManagerListener,
    NarratorState,
)
from vibe.cli.voice_manager.voice_manager_port import TranscribeState


class FakeNarratorManager:
    state = NarratorState.IDLE
    is_playing = False

    def __init__(self) -> None:
        self.listeners: list[NarratorManagerListener] = []
        self.synced = False

    def on_turn_start(self, user_message: str) -> None:
        pass

    def on_user_message(self, message_id: str) -> None:
        pass

    def on_assistant_text(self, content: str) -> None:
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


def test_importing_acp_agent_does_not_import_optional_audio_modules() -> None:
    code = """
import sys
import vibe.acp.agent

blocked = [
    "miniaudio",
    "vibe.cli.narrator_manager.narrator_manager",
    "vibe.cli.voice_manager.voice_manager",
    "vibe.cli.audio_player.audio_player",
    "vibe.cli.audio_recorder.audio_recorder",
    "vibe.cli.transcribe.factory",
    "vibe.cli.tts.factory",
]
loaded = [name for name in blocked if name in sys.modules]
if loaded:
    raise SystemExit(f"unexpected optional modules loaded: {loaded}")
"""

    result = subprocess.run(
        [sys.executable, "-c", code], check=False, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr or result.stdout


def test_lazy_voice_manager_materializes_when_used() -> None:
    config = build_test_app_config(voice_mode_enabled=False)
    factory = MagicMock(return_value=FakeVoiceManager(is_voice_ready=True))
    manager = LazyVoiceManager(lambda: config, factory)

    assert manager.is_enabled is False
    assert manager.transcribe_state == TranscribeState.IDLE
    factory.assert_not_called()

    manager.start_recording()

    factory.assert_called_once()
    assert manager.transcribe_state == TranscribeState.RECORDING


def test_lazy_narrator_manager_materializes_when_enabled_at_startup() -> None:
    config = build_test_app_config(narrator_enabled=True)
    narrator = FakeNarratorManager()
    factory = MagicMock(return_value=narrator)

    manager = LazyNarratorManager(lambda: config, factory)

    factory.assert_called_once()
    assert manager.state == NarratorState.IDLE


def test_lazy_narrator_manager_sync_materializes_after_enable() -> None:
    config = build_test_app_config(narrator_enabled=False)
    narrator = FakeNarratorManager()
    factory = MagicMock(return_value=narrator)
    manager = LazyNarratorManager(lambda: config, factory)

    config = config.model_copy(update={"narrator_enabled": True})
    manager.sync()

    factory.assert_called_once()
    assert narrator.synced is False
