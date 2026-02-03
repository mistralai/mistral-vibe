from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class UpdateCache:
    latest_version: str
    stored_at_timestamp: int
    seen_whats_new_version: str | None = None


class UpdateCacheRepository(Protocol):
    async def get(self) -> UpdateCache | None: ...
    async def set(self, update_cache: UpdateCache) -> None: ...
