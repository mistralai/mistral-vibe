from __future__ import annotations

from vibe.core.config import TTSClient, TTSModelConfig, TTSProviderConfig
from vibe.core.tts.mistral_tts_client import MistralTTSClient
from vibe.core.tts.tts_client_port import TTSClientPort

TTS_CLIENT_MAP: dict[TTSClient, type[TTSClientPort]] = {
    TTSClient.MISTRAL: MistralTTSClient
}


def make_tts_client(
    provider: TTSProviderConfig, model: TTSModelConfig
) -> TTSClientPort:
    return TTS_CLIENT_MAP[provider.client](provider=provider, model=model)
