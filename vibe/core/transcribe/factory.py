from __future__ import annotations

from vibe.core.config import (
    TranscribeClient,
    TranscribeModelConfig,
    TranscribeProviderConfig,
)
from vibe.core.transcribe.mistral_transcribe_client import MistralTranscribeClient
from vibe.core.transcribe.transcribe_client_port import TranscribeClientPort

TRANSCRIBE_CLIENT_MAP: dict[TranscribeClient, type[TranscribeClientPort]] = {
    TranscribeClient.MISTRAL: MistralTranscribeClient
}


def make_transcribe_client(
    provider: TranscribeProviderConfig, model: TranscribeModelConfig
) -> TranscribeClientPort:
    return TRANSCRIBE_CLIENT_MAP[provider.client](provider=provider, model=model)
