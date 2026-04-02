from __future__ import annotations

from collections.abc import Callable

from vibe.core.audio_player import AlreadyPlayingError
from vibe.core.audio_player.audio_player_port import AudioFormat


class FakeAudioPlayer:
    def __init__(self) -> None:
        self._playing = False
        self._on_finished: Callable[[], object] | None = None

    @property
    def is_playing(self) -> bool:
        return self._playing

    def play(
        self,
        audio_data: bytes,
        audio_format: AudioFormat,
        *,
        on_finished: Callable[[], object] | None = None,
    ) -> None:
        if self._playing:
            raise AlreadyPlayingError("Already playing")
        self._playing = True
        self._on_finished = on_finished

    def stop(self) -> None:
        self._playing = False

    def simulate_finished(self) -> None:
        self._playing = False
        if self._on_finished is not None:
            self._on_finished()
