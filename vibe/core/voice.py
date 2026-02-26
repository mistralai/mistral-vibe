from __future__ import annotations

import httpx

from vibe.core.utils import logger

TRANSCRIPTION_URL = "https://api.mistral.ai/v1/audio/transcriptions"
TRANSCRIPTION_MODEL = "voxtral-mini-latest"
TRANSCRIPTION_TIMEOUT = 30.0


async def transcribe(
    wav_data: bytes,
    api_key: str,
    *,
    model: str = TRANSCRIPTION_MODEL,
    timeout: float = TRANSCRIPTION_TIMEOUT,
) -> str:
    """Send WAV audio to the Mistral transcription API and return text.

    Uses the ``/v1/audio/transcriptions`` endpoint (Voxtral Mini Transcribe).
    """
    if not wav_data:
        return ""

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            TRANSCRIPTION_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("recording.wav", wav_data, "audio/wav")},
            data={"model": model},
        )
        response.raise_for_status()

    body = response.json()
    text: str = body.get("text", "")
    logger.debug("Voxtral transcription: %r", text)
    return text.strip()
