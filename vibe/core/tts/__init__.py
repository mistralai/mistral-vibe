from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.tts.tts_client_port import TTSClientPort, TTSResult

if TYPE_CHECKING:
    from vibe.core.config import TTSModelConfig, TTSProviderConfig
    from vibe.core.tts.mistral_tts_client import MistralTTSClient


def make_tts_client(
    provider: TTSProviderConfig, model: TTSModelConfig
) -> TTSClientPort:
    from vibe.core.tts.factory import make_tts_client as factory

    return factory(provider, model)


__all__ = ["MistralTTSClient", "TTSClientPort", "TTSResult", "make_tts_client"]


def __getattr__(name: str) -> object:
    if name == "MistralTTSClient":
        from vibe.core.tts.mistral_tts_client import MistralTTSClient

        return MistralTTSClient
    raise AttributeError(name)
