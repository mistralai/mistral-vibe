from __future__ import annotations

from vibe.app_server.config import AudioProviderView, TTSModelConfigView
from vibe.cli.audio_request_metadata import RequestMetadataGetter
from vibe.cli.tts.mistral_tts_client import MistralTTSClient
from vibe.cli.tts.tts_client_port import TTSClientPort


def make_tts_client(
    provider: AudioProviderView,
    model: TTSModelConfigView,
    metadata_getter: RequestMetadataGetter | None = None,
) -> TTSClientPort:
    return MistralTTSClient(
        provider=provider, model=model, metadata_getter=metadata_getter
    )
