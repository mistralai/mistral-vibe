from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vibe.app_server.config import AudioProviderView, TTSModelConfigView
from vibe.cli.tts.mistral_tts_client import MistralTTSClient
from vibe.cli.tts.tts_client_port import TTSClientPort


def make_tts_client(
    provider: AudioProviderView,
    model: TTSModelConfigView,
    metadata_getter: Callable[[], dict[str, Any]] | None = None,
) -> TTSClientPort:
    return MistralTTSClient(
        provider=provider, model=model, metadata_getter=metadata_getter
    )
