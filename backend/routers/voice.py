"""Voice transcription endpoint."""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from backend.services.voice_transcriber import transcribe_audio

router = APIRouter(prefix="/api/voice", tags=["voice"])


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    """Transcribe an audio file to text."""
    audio_bytes = await file.read()
    text = await transcribe_audio(audio_bytes, mime_type=file.content_type or "audio/webm")
    return {"text": text}
