"""Shared destination shape for an imported conversation."""

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from vibe.core.types import LLMMessage


@dataclass(frozen=True, slots=True)
class VibeSessionPayload:
    """A source conversation translated into Vibe's message model."""

    cwd: Path
    messages: list[LLMMessage]
    title: str | None
    started_at: datetime | None
    source_harness: str
    source_session_id: str
    source_cwd: Path | None

    def __post_init__(self) -> None:
        if not self.source_harness:
            raise ValueError("An imported session needs a source harness")
        if not self.source_session_id:
            raise ValueError("An imported session needs a source session ID")


class ImportedSessionWriter(Protocol):
    """Writes one translated conversation to Vibe's active session backend."""

    def write(self, session: VibeSessionPayload) -> Awaitable[None]: ...
