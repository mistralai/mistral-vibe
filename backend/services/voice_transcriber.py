"""Voice transcription service.

Wraps the Mistral audio transcription endpoint
(``voxtral-mini-transcribe-2-2602``).  Falls back to a stub when the
SDK or key is unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.config import settings
from backend.services.mistral_client import get_client

logger = logging.getLogger(__name__)


async def transcribe_audio(audio_bytes: bytes, mime_type: str = "audio/webm") -> str:
    """Transcribe an audio clip to text.

    Returns the transcribed text or a placeholder in mock mode.
    """
    client = get_client()
    if client is None:
        return "(Mock transcription — no Mistral API key configured.)"

    try:
        # The Mistral audio transcription API expects file-like upload
        import io

        audio_file = io.BytesIO(audio_bytes)
        audio_file.name = f"recording.{_ext(mime_type)}"

        response = client.audio.transcriptions.create(
            model=settings.voxtral_model,
            file=audio_file,
        )
        return response.text  # type: ignore[union-attr]
    except Exception:
        logger.exception("Voice transcription failed")
        return "(Transcription error — please try again.)"


def _ext(mime: str) -> str:
    match mime:
        case "audio/webm":
            return "webm"
        case "audio/wav":
            return "wav"
        case "audio/mp3" | "audio/mpeg":
            return "mp3"
        case "audio/ogg":
            return "ogg"
        case _:
            return "webm"
