from __future__ import annotations

import io
import wave


def decode_wav(audio_data: bytes) -> tuple[int, int, bytes]:
    with wave.open(io.BytesIO(audio_data), "rb") as wf:
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        pcm_data = wf.readframes(wf.getnframes())
    return sample_rate, channels, pcm_data
