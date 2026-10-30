from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

AUTO_THEME = "auto"
DEFAULT_THEME = AUTO_THEME
DEFAULT_LOG_LEVEL = "WARNING"

type AudioClient = Literal["mistral"]
THINKING_LEVELS: tuple[str, ...] = ("off", "low", "medium", "high", "xhigh", "max")
# Off, a standard level, or a level of a model's own that its `thinking_levels`
# lists: sent to the provider as written. The schema lists the standard ones.
type ThinkingLevel = Annotated[
    str, Field(pattern=r"\S", examples=list(THINKING_LEVELS))
]
# The levels a model offers when its config does not narrow them. xhigh is
# accepted everywhere but offered only by a model that lists it.
DEFAULT_THINKING_LEVELS: tuple[ThinkingLevel, ...] = (
    "off",
    "low",
    "medium",
    "high",
    "max",
)

type TranscriptionEncoding = Literal["pcm_s16le"]
type SpeechOutputFormat = Literal["pcm", "wav", "mp3", "flac", "opus"]
