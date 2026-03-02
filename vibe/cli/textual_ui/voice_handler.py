# vibe/cli/textual_ui/voice_handler.py

import os
import asyncio
from typing import AsyncIterator, Callable, Optional
from pathlib import Path
from dotenv import load_dotenv
from mistralai import Mistral
from mistralai.models import AudioFormat, RealtimeTranscriptionError, TranscriptionStreamTextDelta

class MistralVoiceHandler:
    def __init__(self, on_text_callback: Callable[[str], None]):
        """
        :param on_text_callback: Fonction appelée pour chaque nouveau fragment de texte.
        """
        self.on_text_callback = on_text_callback
        self.is_recording = False

        # Initialisation de l'API Mistral
        env_path = Path.home() / ".vibe" / ".env"
        load_dotenv(dotenv_path=env_path)
        api_key = os.getenv("MISTRAL_API_KEY")

        self.client = Mistral(api_key=api_key)
        self.audio_format = AudioFormat(encoding="pcm_s16le", sample_rate=16000)

    async def _iter_microphone(self, sample_rate: int, chunk_duration_ms: int) -> AsyncIterator[bytes]:
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
            # On boucle tant que le flag is_recording est actif
            while self.is_recording:
                data = await loop.run_in_executor(None, stream.read, chunk_samples, False)
                yield data
        finally:
            stream.stop_stream()
            stream.close()
            p.terminate()

    async def start_recording(self):
        """Lance la capture et la transcription."""
        if self.is_recording:
            return

        self.is_recording = True
        audio_stream = self._iter_microphone(
            sample_rate=self.audio_format.sample_rate,
            chunk_duration_ms=480
        )

        try:
            async for event in self.client.audio.realtime.transcribe_stream(
                audio_stream=audio_stream,
                model="voxtral-mini-transcribe-realtime-2602",
                audio_format=self.audio_format,
            ):
                if isinstance(event, TranscriptionStreamTextDelta):
                    self.on_text_callback(event.text)

                elif isinstance(event, RealtimeTranscriptionError):
                    self.on_text_callback(f" [Erreur: {event}] ")
                    break

                if not self.is_recording:
                    break
        except Exception as e:
            self.on_text_callback(f" [Erreur système: {e}] ")
        finally:
            self.is_recording = False

    def stop_recording(self):
        """Arrête la boucle de capture."""
        self.is_recording = False
