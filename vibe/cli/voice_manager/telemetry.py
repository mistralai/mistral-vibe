from __future__ import annotations

from dataclasses import dataclass, field
import time


@dataclass
class TranscriptionTrackingState:
    recording_id: str = ""
    start_time: float = field(default_factory=time.monotonic)
    accumulated_transcript_length: int = 0
    last_recording_duration_ms: float | None = None

    def reset(self) -> None:
        self.recording_id = ""
        self.start_time = time.monotonic()
        self.accumulated_transcript_length = 0
        self.last_recording_duration_ms = None

    def set_recording_id(self, recording_id: str) -> None:
        self.recording_id = recording_id

    def record_text(self, text: str) -> None:
        self.accumulated_transcript_length += len(text)

    def elapsed_ms(self) -> float:
        return (time.monotonic() - self.start_time) * 1000

    def set_recording_duration(self, duration_s: float) -> None:
        self.last_recording_duration_ms = duration_s * 1000
