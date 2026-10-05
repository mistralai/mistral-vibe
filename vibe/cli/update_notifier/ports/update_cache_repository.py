from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class UpdateCache:
    latest_version: str
    stored_at_timestamp: int
    seen_whats_new_version: str | None = None
    dismissed_version: str | None = None
    # The Rust client's `source_stored_at` pairing key, only written to void a
    # stale tag (never a real timestamp), so `set` can break what the on-disk
    # key merge would otherwise preserve.
    source_stored_at: int | None = None


class UpdateCacheRepository(Protocol):
    async def get(self) -> UpdateCache | None: ...
    async def set(self, update_cache: UpdateCache) -> None: ...
