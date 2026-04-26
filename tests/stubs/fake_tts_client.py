from __future__ import annotations

from typing import Any

from vibe.core.tts.tts_client_port import TTSResult


class FakeTTSClient:
    def __init__(
        self, *_args: Any, result: TTSResult | None = None, **_kwargs: Any
    ) -> None:
        self._result: TTSResult = result or TTSResult(audio_data=b"fake-audio")

    def set_result(self, result: TTSResult) -> None:
        self._result = result

    async def speak(self, text: str) -> TTSResult:
        return self._result

    async def close(self) -> None:
        pass
