from __future__ import annotations

from vibe.cli.update_notifier.ports.update_cache_repository import (
    UpdateCache,
    UpdateCacheRepository,
)


class FakeUpdateCacheRepository(UpdateCacheRepository):
    def __init__(self, update_cache: UpdateCache | None = None) -> None:
        self.update_cache: UpdateCache | None = update_cache

    async def get(self) -> UpdateCache | None:
        return self.update_cache

    async def set(self, update_cache: UpdateCache) -> None:
        self.update_cache = update_cache
