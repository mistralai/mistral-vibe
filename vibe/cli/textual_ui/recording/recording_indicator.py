from __future__ import annotations

from textual.timer import Timer
from textual.widgets import Static

from vibe.cli.voice_manager.voice_manager_port import (
    TranscribeState,
    VoiceManagerListener,
    VoiceManagerPort,
)

PEAK_BLOCKS = "▁▂▃▄▅▆▇█"
FILL_BLOCKS = "▏▎▍▌▋▊▉█"
PEAK_POLL_INTERVAL = 0.05


class RecordingIndicator(VoiceManagerListener, Static):
    def __init__(self, voice_manager: VoiceManagerPort) -> None:
        super().__init__(PEAK_BLOCKS[0], id="recording-indicator")
        self._voice_manager = voice_manager
        self._peak_timer: Timer | None = None
        self._flushing_animation_timer: Timer | None = None

    def on_mount(self) -> None:
        self._voice_manager.add_listener(self)
        if self._voice_manager.transcribe_state == TranscribeState.RECORDING:
            self._start_peak_polling()

    def on_unmount(self) -> None:
        self._voice_manager.remove_listener(self)
        self._stop_peak_polling()
        self._stop_flushing_animation_timer()

    def on_transcribe_state_change(self, state: TranscribeState) -> None:
        match state:
            case TranscribeState.RECORDING:
                self._start_peak_polling()
            case TranscribeState.FLUSHING:
                self._stop_peak_polling()
                self._start_flushing_animation_timer()
            case TranscribeState.IDLE:
                self._stop_peak_polling()
                self._stop_flushing_animation_timer()

    def _start_peak_polling(self) -> None:
        self._peak_timer = self.set_interval(
            PEAK_POLL_INTERVAL, self._poll_peak, pause=False
        )

    def _poll_peak(self) -> None:
        if self._voice_manager.transcribe_state != TranscribeState.RECORDING:
            return
        index = min(
            int(self._voice_manager.peak * len(PEAK_BLOCKS)), len(PEAK_BLOCKS) - 1
        )
        self.update(PEAK_BLOCKS[index])

    def _stop_peak_polling(self) -> None:
        if self._peak_timer:
            self._peak_timer.stop()
            self._peak_timer = None

    def _start_flushing_animation_timer(self) -> None:
        self._processing_index = 0
        self.update(FILL_BLOCKS[0])
        self._flushing_animation_timer = self.set_interval(
            0.1, self._advance_flushing_animation
        )

    def _advance_flushing_animation(self) -> None:
        if self._voice_manager.transcribe_state != TranscribeState.FLUSHING:
            return
        self._processing_index = (self._processing_index + 1) % len(FILL_BLOCKS)
        self.update(FILL_BLOCKS[self._processing_index])

    def _stop_flushing_animation_timer(self) -> None:
        if self._flushing_animation_timer:
            self._flushing_animation_timer.stop()
            self._flushing_animation_timer = None
