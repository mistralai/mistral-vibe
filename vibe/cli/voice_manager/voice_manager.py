from __future__ import annotations

from asyncio import CancelledError, Task, create_task, wait_for
from collections.abc import Callable

from vibe.cli.voice_manager.voice_manager_port import (
    RecordingStartError,
    TranscribeState,
    VoiceManagerListener,
    VoiceToggleResult,
)
from vibe.core.audio_recorder import AudioRecorderPort
from vibe.core.audio_recorder.audio_recorder_port import (
    AlreadyRecordingError,
    AudioBackendUnavailableError,
    NoAudioInputDeviceError,
    RecordingMode,
)
from vibe.core.config import VibeConfig
from vibe.core.logger import logger
from vibe.core.transcribe.transcribe_client_port import (
    TranscribeClientPort,
    TranscribeDone,
    TranscribeError,
    TranscribeSessionCreated,
    TranscribeTextDelta,
)

TRANSCRIPTION_DRAIN_TIMEOUT = 10.0


class VoiceManager:
    def __init__(
        self,
        config_getter: Callable[[], VibeConfig],
        audio_recorder: AudioRecorderPort,
        transcribe_client: TranscribeClientPort | None,
    ) -> None:
        self._config_getter = config_getter
        self._audio_recorder = audio_recorder
        self._transcribe_client = transcribe_client
        self._transcribe_state = TranscribeState.IDLE
        self._transcribe_task: Task[None] | None = None
        self._listeners: list[VoiceManagerListener] = []

    @property
    def is_enabled(self) -> bool:
        return self._config_getter().voice_mode_enabled

    @property
    def transcribe_state(self) -> TranscribeState:
        return self._transcribe_state

    @property
    def peak(self) -> float:
        return self._audio_recorder.peak

    def toggle_voice_mode(self) -> VoiceToggleResult:
        new_state = not self.is_enabled
        if not new_state:
            self.cancel_recording()

        VibeConfig.save_updates({"voice_mode_enabled": new_state})

        for listener in self._listeners:
            try:
                listener.on_voice_mode_change(new_state)
            except Exception:
                logger.error("Listener raised during voice mode change", exc_info=True)

        return VoiceToggleResult(enabled=new_state)

    def start_recording(self, mode: RecordingMode = RecordingMode.STREAM) -> None:
        if self._transcribe_state != TranscribeState.IDLE:
            return

        if self._transcribe_client is None:
            logger.warning(
                "Failed to start recording as the transcribe client is missing"
            )
            raise RecordingStartError("Transcribe client is not available")

        model = self._config_getter().get_active_transcribe_model()

        try:
            self._audio_recorder.start(mode, sample_rate=model.sample_rate)
        except AlreadyRecordingError:
            raise RecordingStartError("Recording is already in progress")
        except AudioBackendUnavailableError:
            raise RecordingStartError("Audio backend is unavailable")
        except NoAudioInputDeviceError:
            raise RecordingStartError("No audio input device found")

        self._set_state(TranscribeState.RECORDING)
        self._transcribe_task = create_task(self._run_transcription())

    async def stop_recording(self) -> None:
        if self._transcribe_state != TranscribeState.RECORDING:
            return
        should_flush_queue = self._audio_recorder.mode == RecordingMode.STREAM

        if should_flush_queue:
            self._set_state(TranscribeState.FLUSHING)
        self._audio_recorder.stop(wait_for_queue_drained=should_flush_queue)

        if self._transcribe_task is not None:
            try:
                await wait_for(
                    self._transcribe_task, timeout=TRANSCRIPTION_DRAIN_TIMEOUT
                )
            except TimeoutError:
                logger.warning("Transcription task timed out, cancelling")
                self._transcribe_task.cancel()
            except CancelledError:
                pass
            self._transcribe_task = None

        if self._transcribe_state != TranscribeState.IDLE:
            self._set_state(TranscribeState.IDLE)

    def cancel_recording(self) -> None:
        if self._transcribe_state == TranscribeState.IDLE:
            return

        self._audio_recorder.cancel()

        if self._transcribe_task is not None:
            self._transcribe_task.cancel()
            self._transcribe_task = None

        self._set_state(TranscribeState.IDLE)

    def add_listener(self, listener: VoiceManagerListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: VoiceManagerListener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    async def _run_transcription(self) -> None:
        if self._transcribe_client is None:
            return

        try:
            audio_stream = self._audio_recorder.audio_stream()

            async for event in self._transcribe_client.transcribe(audio_stream):
                match event:
                    case TranscribeTextDelta(text=text):
                        for listener in self._listeners:
                            try:
                                listener.on_transcribe_text(text)
                            except Exception:
                                logger.error(
                                    "Listener raised during transcribe text",
                                    exc_info=True,
                                )
                    case TranscribeDone():
                        pass
                    case TranscribeError(message=msg):
                        raise RuntimeError(msg)
                    case TranscribeSessionCreated():
                        pass
            if self._transcribe_state != TranscribeState.IDLE:
                self._set_state(TranscribeState.IDLE)
        except CancelledError:
            raise
        except Exception as exc:
            logger.error("Transcription failed", exc_info=exc)
            self._audio_recorder.cancel()
            if self._transcribe_state != TranscribeState.IDLE:
                self._set_state(TranscribeState.IDLE)

    def _set_state(self, state: TranscribeState) -> None:
        if self._transcribe_state == state:
            return

        self._transcribe_state = state
        for listener in self._listeners:
            try:
                listener.on_transcribe_state_change(state)
            except Exception:
                logger.error("Listener raised during state change", exc_info=True)
