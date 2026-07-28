from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from vibe.app_server.config import ConfigView
from vibe.cli.narrator_manager.narrator_manager_port import (
    NarratorManagerListener,
    NarratorManagerPort,
    NarratorState,
)
from vibe.cli.turn_summary import TurnSummaryGenerator
from vibe.cli.voice_manager.voice_manager_port import (
    TranscribeState,
    VoiceManagerListener,
    VoiceManagerPort,
)
from vibe.observability.logging import logger
from vibe.utils.audio import RecordingMode

if TYPE_CHECKING:
    from vibe.cli.telemetry import ClientTelemetry


def check_audio_available() -> str | None:
    from vibe.cli.audio_player.audio_player import check_audio_available as check

    return check()


class LazyVoiceManager:
    def __init__(
        self,
        config_getter: Callable[[], ConfigView],
        factory: Callable[[], VoiceManagerPort],
    ) -> None:
        self._config_getter = config_getter
        self._factory = factory
        self._manager: VoiceManagerPort | None = None
        self._listeners: list[VoiceManagerListener] = []
        if self._config_getter().voice_mode_enabled:
            self._materialize()

    @property
    def is_enabled(self) -> bool:
        if self._manager is not None:
            return self._manager.is_enabled
        return self._config_getter().voice_mode_enabled

    @property
    def transcribe_state(self) -> TranscribeState:
        if self._manager is None:
            return TranscribeState.IDLE
        return self._manager.transcribe_state

    @property
    def peak(self) -> float:
        if self._manager is None:
            return 0.0
        return self._manager.peak

    def apply_enabled(self, enabled: bool) -> None:
        if self._manager is None and not enabled:
            return
        self._materialize().apply_enabled(enabled)

    def start_recording(self, mode: RecordingMode = RecordingMode.STREAM) -> None:
        self._materialize().start_recording(mode)

    async def stop_recording(self) -> None:
        if self._manager is not None:
            await self._manager.stop_recording()

    def cancel_recording(self) -> None:
        if self._manager is not None:
            self._manager.cancel_recording()

    def add_listener(self, listener: VoiceManagerListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)
        if self._manager is not None:
            self._manager.add_listener(listener)

    def remove_listener(self, listener: VoiceManagerListener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass
        if self._manager is not None:
            self._manager.remove_listener(listener)

    async def close(self) -> None:
        if self._manager is not None:
            await self._manager.close()

    def _materialize(self) -> VoiceManagerPort:
        if self._manager is None:
            self._manager = self._factory()
            for listener in self._listeners:
                self._manager.add_listener(listener)
        return self._manager


class LazyNarratorManager:
    def __init__(
        self,
        config_getter: Callable[[], ConfigView],
        factory: Callable[[], NarratorManagerPort],
    ) -> None:
        self._config_getter = config_getter
        self._factory = factory
        self._manager: NarratorManagerPort | None = None
        self._listeners: list[NarratorManagerListener] = []
        if self._config_getter().narrator_enabled:
            self._materialize()

    @property
    def state(self) -> NarratorState:
        if self._manager is None:
            return NarratorState.IDLE
        return self._manager.state

    @property
    def is_playing(self) -> bool:
        if self._manager is None:
            return False
        return self._manager.is_playing

    def on_turn_start(self, user_message: str) -> None:
        if self._manager is not None:
            self._manager.on_turn_start(user_message)

    def on_user_message(self, message_id: str) -> None:
        if self._manager is not None:
            self._manager.on_user_message(message_id)

    def on_assistant_text(self, content: str) -> None:
        if self._manager is not None:
            self._manager.on_assistant_text(content)

    def on_turn_error(self, message: str) -> None:
        if self._manager is not None:
            self._manager.on_turn_error(message)

    def on_turn_cancel(self) -> None:
        if self._manager is not None:
            self._manager.on_turn_cancel()

    def on_turn_end(self) -> None:
        if self._manager is not None:
            self._manager.on_turn_end()

    def cancel(self) -> None:
        if self._manager is not None:
            self._manager.cancel()

    def sync(self) -> None:
        if self._manager is None:
            if self._config_getter().narrator_enabled:
                self._materialize()
            return
        self._manager.sync()

    def add_listener(self, listener: NarratorManagerListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)
        if self._manager is not None:
            self._manager.add_listener(listener)

    def remove_listener(self, listener: NarratorManagerListener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass
        if self._manager is not None:
            self._manager.remove_listener(listener)

    async def close(self) -> None:
        if self._manager is not None:
            await self._manager.close()

    def _materialize(self) -> NarratorManagerPort:
        if self._manager is None:
            self._manager = self._factory()
            for listener in self._listeners:
                self._manager.add_listener(listener)
        return self._manager


def create_default_voice_manager(
    config_getter: Callable[[], ConfigView], telemetry_client: ClientTelemetry | None
) -> VoiceManagerPort:
    return LazyVoiceManager(
        config_getter,
        lambda: _create_real_voice_manager(config_getter, telemetry_client),
    )


def create_default_narrator_manager(
    config_getter: Callable[[], ConfigView],
    summary_generator: TurnSummaryGenerator,
    telemetry_client: ClientTelemetry | None,
) -> NarratorManagerPort:
    return LazyNarratorManager(
        config_getter,
        lambda: _create_real_narrator_manager(
            config_getter, summary_generator, telemetry_client
        ),
    )


def _create_real_voice_manager(
    config_getter: Callable[[], ConfigView], telemetry_client: ClientTelemetry | None
) -> VoiceManagerPort:
    from vibe.cli.audio_recorder.audio_recorder import AudioRecorder
    from vibe.cli.transcribe.factory import make_transcribe_client
    from vibe.cli.voice_manager.voice_manager import VoiceManager

    config = config_getter()
    try:
        model = config.transcription.model
        provider = config.transcription.provider
        transcribe_client = make_transcribe_client(
            provider,
            model,
            metadata_getter=(
                None
                if telemetry_client is None
                else telemetry_client.build_request_metadata
            ),
        )
    except KeyError as exc:
        logger.error(
            "Failed to initialize transcription, check transcribe model configuration",
            exc_info=exc,
        )
        transcribe_client = None

    return VoiceManager(
        config_getter,
        audio_recorder=AudioRecorder(),
        transcribe_client=transcribe_client,
        telemetry_client=telemetry_client,
    )


def _create_real_narrator_manager(
    config_getter: Callable[[], ConfigView],
    summary_generator: TurnSummaryGenerator,
    telemetry_client: ClientTelemetry | None,
) -> NarratorManagerPort:
    from vibe.cli.audio_player.audio_player import AudioPlayer
    from vibe.cli.narrator_manager.narrator_manager import NarratorManager

    return NarratorManager(
        config_getter=config_getter,
        audio_player=AudioPlayer(),
        summary_generator=summary_generator,
        telemetry_client=telemetry_client,
    )
