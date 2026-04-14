from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from vibe.cli.narrator_manager.narrator_manager_port import (
    NarratorManagerListener,
    NarratorState,
)
from vibe.cli.narrator_manager.telemetry import ReadAloudTrackingState
from vibe.cli.turn_summary import (
    NoopTurnSummary,
    TurnSummaryResult,
    TurnSummaryTracker,
    create_narrator_backend,
)
from vibe.core.audio_player.audio_player_port import AudioFormat
from vibe.core.logger import logger
from vibe.core.tts.factory import make_tts_client

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from vibe.cli.turn_summary import TurnSummaryPort
    from vibe.core.audio_player.audio_player_port import AudioPlayerPort
    from vibe.core.config import VibeConfig
    from vibe.core.telemetry.send import TelemetryClient
    from vibe.core.tts.tts_client_port import TTSClientPort
    from vibe.core.types import BaseEvent


class NarratorManager:
    def __init__(
        self,
        config_getter: Callable[[], VibeConfig],
        audio_player: AudioPlayerPort,
        telemetry_client: TelemetryClient | None = None,
    ) -> None:
        self._config_getter = config_getter
        self._audio_player = audio_player
        self._telemetry_client = telemetry_client
        config = config_getter()
        self._turn_summary: TurnSummaryPort = self._make_turn_summary(config)
        self._turn_summary.on_summary = self._on_turn_summary
        self._tts_client: TTSClientPort | None = self._make_tts_client(config)
        self._state = NarratorState.IDLE
        self._speak_task: asyncio.Task[None] | None = None
        self._cancel_summary: Callable[[], bool] | None = None
        self._close_tasks: set[asyncio.Task[Any]] = set()
        self._listeners: list[NarratorManagerListener] = []
        self._tracking = ReadAloudTrackingState()

    @property
    def state(self) -> NarratorState:
        return self._state

    @property
    def is_playing(self) -> bool:
        return self._audio_player.is_playing

    @property
    def turn_summary(self) -> TurnSummaryPort:
        return self._turn_summary

    @turn_summary.setter
    def turn_summary(self, value: TurnSummaryPort) -> None:
        old = self._turn_summary
        self._turn_summary = value
        self._turn_summary.on_summary = self._on_turn_summary
        task = asyncio.create_task(old.close())
        self._close_tasks.add(task)
        task.add_done_callback(self._close_tasks.discard)

    @property
    def tts_client(self) -> TTSClientPort | None:
        return self._tts_client

    @tts_client.setter
    def tts_client(self, value: TTSClientPort | None) -> None:
        old = self._tts_client
        self._tts_client = value
        if old is not None:
            task = asyncio.create_task(old.close())
            self._close_tasks.add(task)
            task.add_done_callback(self._close_tasks.discard)

    def on_turn_start(self, user_message: str) -> None:
        self._turn_summary.start_turn(user_message)

    def on_turn_event(self, event: BaseEvent) -> None:
        self._turn_summary.track(event)

    def on_turn_error(self, message: str) -> None:
        self._turn_summary.set_error(message)

    def on_turn_cancel(self) -> None:
        self._turn_summary.cancel_turn()

    def on_turn_end(self) -> None:
        cancel_summary = self._turn_summary.end_turn()
        if (
            cancel_summary is not None
            and self._config_getter().narrator_enabled
            and self._tts_client is not None
        ):
            self._cancel_summary = cancel_summary
            self._set_state(NarratorState.SUMMARIZING)
            self._tracking.reset()
            self._on_read_aloud_requested()

    def cancel(self) -> None:
        if self._state != NarratorState.IDLE:
            self._on_read_aloud_ended("canceled")
        if self._cancel_summary is not None:
            self._cancel_summary()
            self._cancel_summary = None
        if self._speak_task is not None and not self._speak_task.done():
            self._speak_task.cancel()
            self._speak_task = None
        self._audio_player.stop()
        self._set_state(NarratorState.IDLE)

    def sync(self) -> None:
        self.cancel()
        config = self._config_getter()
        self.turn_summary = self._make_turn_summary(config)
        self.tts_client = self._make_tts_client(config)

    @staticmethod
    def _make_turn_summary(config: VibeConfig) -> NoopTurnSummary | TurnSummaryTracker:
        if not config.narrator_enabled:
            return NoopTurnSummary()
        result = create_narrator_backend(config)
        if result is None:
            return NoopTurnSummary()
        backend, model = result
        return TurnSummaryTracker(backend=backend, model=model)

    @staticmethod
    def _make_tts_client(config: VibeConfig) -> TTSClientPort | None:
        if not config.narrator_enabled:
            return None
        try:
            model = config.get_active_tts_model()
            provider = config.get_tts_provider_for_model(model)
            return make_tts_client(provider, model)
        except (ValueError, KeyError) as exc:
            logger.error("Failed to initialize TTS client", exc_info=exc)
            return None

    def add_listener(self, listener: NarratorManagerListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: NarratorManagerListener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    async def close(self) -> None:
        self.cancel()
        await self._turn_summary.close()
        if self._tts_client is not None:
            await self._tts_client.close()
        for task in self._close_tasks:
            task.cancel()
        await asyncio.gather(*self._close_tasks, return_exceptions=True)
        self._close_tasks.clear()

    def _on_turn_summary(self, result: TurnSummaryResult) -> None:
        self._cancel_summary = None
        if result.generation != self._turn_summary.generation:
            self._set_state(NarratorState.IDLE)
            return
        if result.summary is None:
            self._set_state(NarratorState.IDLE)
            return
        if self._tts_client is not None:
            self._speak_task = asyncio.create_task(self._speak_summary(result.summary))
        else:
            self._set_state(NarratorState.IDLE)

    async def _speak_summary(self, text: str) -> None:
        if self._tts_client is None:
            return
        try:
            loop = asyncio.get_running_loop()
            tts_result = await self._tts_client.speak(text)
            self._set_state(NarratorState.SPEAKING)
            self._tracking.mark_play_started()
            self._on_read_aloud_play_started()
            self._audio_player.play(
                tts_result.audio_data,
                AudioFormat.WAV,
                on_finished=lambda: loop.call_soon_threadsafe(
                    self._on_playback_finished
                ),
            )
        except Exception as exc:
            logger.warning("TTS speak failed", exc_info=True)
            self._on_read_aloud_ended("error", error_type=type(exc).__name__)
            self._set_state(NarratorState.IDLE)

    def _on_playback_finished(self) -> None:
        if self._state != NarratorState.SPEAKING:
            return
        self._on_read_aloud_ended("completed")
        self._set_state(NarratorState.IDLE)

    def _on_read_aloud_requested(self) -> None:
        if not self._telemetry_client:
            return
        self._telemetry_client.send_telemetry_event(
            "vibe.read_aloud.requested",
            {
                "read_aloud_session_id": self._tracking.session_id,
                "trigger": "autoplay_next_message",
            },
        )

    def _on_read_aloud_play_started(self) -> None:
        if not self._telemetry_client:
            return
        self._telemetry_client.send_telemetry_event(
            "vibe.read_aloud.play_started",
            {
                "read_aloud_session_id": self._tracking.session_id,
                "time_to_first_read_s": self._tracking.time_to_first_read_s(),
                "speed_selection": None,
            },
        )

    def _on_read_aloud_ended(
        self, status: str, *, error_type: str | None = None
    ) -> None:
        if not self._telemetry_client:
            return
        self._telemetry_client.send_telemetry_event(
            "vibe.read_aloud.ended",
            {
                "read_aloud_session_id": self._tracking.session_id,
                "status": status,
                "error_type": error_type,
                "speed_selection": None,
                "elapsed_seconds": self._tracking.elapsed_since_play_s(),
            },
        )

    def _set_state(self, state: NarratorState) -> None:
        self._state = state
        for listener in list(self._listeners):
            try:
                listener.on_narrator_state_change(state)
            except Exception:
                logger.warning("Narrator listener error", exc_info=True)
