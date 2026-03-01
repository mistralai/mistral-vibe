from mistralai import Mistral
from mistralai.extra.realtime import UnknownRealtimeEvent
from mistralai.models import AudioFormat, RealtimeTranscriptionError, RealtimeTranscriptionSessionCreated, TranscriptionStreamDone, TranscriptionStreamTextDelta
import os
import asyncio
import sys
from typing import AsyncIterator
from pathlib import Path
from dotenv import load_dotenv


vibe_env_path = Path.home() / ".vibe" / ".env"
loaded = load_dotenv(dotenv_path=vibe_env_path)
api_key = os.getenv("MISTRAL_API_KEY")
#api_key = "p3j6jfr3cCwD6INiffuDPLq5QtSFJDhJ"
client = Mistral(api_key=api_key)

#microphone is always pcm_s16le here
audio_format = AudioFormat(encoding="pcm_s16le", sample_rate=16000)

async def iter_microphone(
    *,
    sample_rate: int,
    chunk_duration_ms: int,
) -> AsyncIterator[bytes]:
    """
    Yield microphone PCM chunks using PyAudio (16-bit mono).
    Encoding is always pcm_s16le.
    """
    import pyaudio

    p = pyaudio.PyAudio()
    chunk_samples = int(sample_rate * chunk_duration_ms / 1000)

    stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=sample_rate,
        input=True,
        frames_per_buffer=chunk_samples,
    )

    loop = asyncio.get_running_loop()
    try:
        while True:
            # stream.read is blocking; run it off-thread
            data = await loop.run_in_executor(None, stream.read, chunk_samples, False)
            yield data
    finally:
        stream.stop_stream()
        stream.close()
        p.terminate()

audio_stream = iter_microphone(sample_rate=audio_format.sample_rate, chunk_duration_ms=480)

async def main():
    try:
        async for event in client.audio.realtime.transcribe_stream(
            audio_stream=audio_stream, # audio stream corresponds to any iterable of bytes
            model="voxtral-mini-transcribe-realtime-2602",
            audio_format=audio_format,
        ):
            if isinstance(event, RealtimeTranscriptionSessionCreated):
                print(f"Session created.")
            elif isinstance(event, TranscriptionStreamTextDelta):
                print(event.text, end="", flush=True)
            elif isinstance(event, TranscriptionStreamDone):
                print("Transcription done.")
            elif isinstance(event, RealtimeTranscriptionError):
                print(f"Error: {event}")
            elif isinstance(event, UnknownRealtimeEvent):
                print(f"Unknown event: {event}")
                continue
    except KeyboardInterrupt:
        print("Stopping...")

sys.exit(asyncio.run(main()))
