from __future__ import annotations

from tests.stubs.fake_audio_recorder import FakeAudioRecorder
from vibe.cli.voice_manager import VoiceToggleResult
from vibe.cli.voice_manager.voice_manager_port import (
    TranscribeState,
    VoiceManagerListener,
)
from vibe.core.audio_recorder import AudioRecorderPort
from vibe.core.audio_recorder.audio_recorder_port import RecordingMode


class FakeVoiceManager:
    def __init__(
        self,
        *,
        is_voice_ready: bool = False,
        audio_recorder: AudioRecorderPort | None = None,
    ) -> None:
        self._enabled = is_voice_ready
        self._audio_recorder: AudioRecorderPort = audio_recorder or FakeAudioRecorder()
        self._transcribe_state = TranscribeState.IDLE
        self._listeners: list[VoiceManagerListener] = []

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def transcribe_state(self) -> TranscribeState:
        return self._transcribe_state

    @property
    def peak(self) -> float:
        return self._audio_recorder.peak

    def toggle_voice_mode(self) -> VoiceToggleResult:
        self._enabled = not self._enabled
        if not self._enabled:
            self.cancel_recording()
        for listener in self._listeners:
            listener.on_voice_mode_change(self._enabled)
        return VoiceToggleResult(enabled=self._enabled)

    def start_recording(self, mode: RecordingMode = RecordingMode.STREAM) -> None:
        self._set_state(TranscribeState.RECORDING)

    async def stop_recording(self) -> None:
        self._set_state(TranscribeState.FLUSHING)
        self._set_state(TranscribeState.IDLE)

    def cancel_recording(self) -> None:
        if self._transcribe_state == TranscribeState.IDLE:
            return
        if self._transcribe_state == TranscribeState.RECORDING:
            pass  # fake: no actual audio to cancel
        self._set_state(TranscribeState.IDLE)

    def add_listener(self, listener: VoiceManagerListener) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def remove_listener(self, listener: VoiceManagerListener) -> None:
        try:
            self._listeners.remove(listener)
        except ValueError:
            pass

    def _set_state(self, state: TranscribeState) -> None:
        if self._transcribe_state == state:
            return
        self._transcribe_state = state
        for listener in self._listeners:
            listener.on_transcribe_state_change(state)
