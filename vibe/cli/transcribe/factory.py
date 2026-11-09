from __future__ import annotations

from vibe.app_server.config import AudioProviderView, TranscribeModelConfigView
from vibe.cli.audio_request_metadata import RequestMetadataGetter
from vibe.cli.transcribe.mistral_transcribe_client import MistralTranscribeClient
from vibe.cli.transcribe.transcribe_client_port import TranscribeClientPort


def make_transcribe_client(
    provider: AudioProviderView,
    model: TranscribeModelConfigView,
    metadata_getter: RequestMetadataGetter | None = None,
) -> TranscribeClientPort:
    return MistralTranscribeClient(
        provider=provider, model=model, metadata_getter=metadata_getter
    )
