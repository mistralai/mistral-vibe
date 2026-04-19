from __future__ import annotations

from dataclasses import dataclass
import time
import uuid


@dataclass
class ReadAloudTrackingState:
    session_id: str = ""
    request_time: float = 0.0
    play_start_time: float = 0.0

    def reset(self) -> None:
        self.session_id = str(uuid.uuid4())
        self.request_time = time.monotonic()
        self.play_start_time = 0.0

    def mark_play_started(self) -> None:
        self.play_start_time = time.monotonic()

    def time_to_first_read_s(self) -> float:
        if self.play_start_time == 0.0 or self.request_time == 0.0:
            return 0.0
        return self.play_start_time - self.request_time

    def elapsed_since_play_s(self) -> float:
        if self.play_start_time == 0.0:
            return 0.0
        return time.monotonic() - self.play_start_time
