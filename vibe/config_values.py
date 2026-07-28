from __future__ import annotations

from typing import Literal

AUTO_THEME = "auto"
FALLBACK_THEME = "ansi-dark"
DEFAULT_THEME = AUTO_THEME

type AudioClient = Literal["mistral"]
type ThinkingLevel = Literal["off", "low", "medium", "high", "max"]
THINKING_LEVELS: tuple[ThinkingLevel, ...] = ("off", "low", "medium", "high", "max")

type TranscriptionEncoding = Literal["pcm_s16le"]
type SpeechOutputFormat = Literal["pcm", "wav", "mp3", "flac", "opus"]
