from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.core.transcribe.transcribe_client_port import (
    TranscribeClientPort,
    TranscribeDone,
    TranscribeError,
    TranscribeEvent,
    TranscribeSessionCreated,
    TranscribeTextDelta,
)

if TYPE_CHECKING:
    from vibe.core.config import TranscribeModelConfig, TranscribeProviderConfig
    from vibe.core.transcribe.mistral_transcribe_client import MistralTranscribeClient


def make_transcribe_client(
    provider: TranscribeProviderConfig, model: TranscribeModelConfig
) -> TranscribeClientPort:
    from vibe.core.transcribe.factory import make_transcribe_client as factory

    return factory(provider, model)


__all__ = [
    "MistralTranscribeClient",
    "TranscribeClientPort",
    "TranscribeDone",
    "TranscribeError",
    "TranscribeEvent",
    "TranscribeSessionCreated",
    "TranscribeTextDelta",
    "make_transcribe_client",
]


def __getattr__(name: str) -> object:
    if name == "MistralTranscribeClient":
        from vibe.core.transcribe.mistral_transcribe_client import (
            MistralTranscribeClient,
        )

        return MistralTranscribeClient
    raise AttributeError(name)
