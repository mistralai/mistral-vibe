from __future__ import annotations

from dataclasses import dataclass

from vibe.app_server.models import PublicHistoryEntry


@dataclass(slots=True)
class SessionHistory:
    base: list[PublicHistoryEntry]

    def replace(self, entries: list[PublicHistoryEntry]) -> None:
        self.base = entries

    def all(self, current: list[PublicHistoryEntry]) -> list[PublicHistoryEntry]:
        return [*self.base, *current]
