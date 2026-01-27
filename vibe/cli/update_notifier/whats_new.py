from __future__ import annotations

import time

from vibe import VIBE_ROOT
from vibe.cli.update_notifier.ports.update_cache_repository import (
    UpdateCache,
    UpdateCacheRepository,
)


async def should_show_whats_new(
    current_version: str, repository: UpdateCacheRepository
) -> bool:
    cache = await repository.get()
    if cache is None:
        return False
    return cache.seen_whats_new_version != current_version


def load_whats_new_content() -> str | None:
    whats_new_file = VIBE_ROOT / "whats_new.md"
    if not whats_new_file.exists():
        return None
    try:
        content = whats_new_file.read_text(encoding="utf-8").strip()
        return content if content else None
    except OSError:
        return None


async def mark_version_as_seen(version: str, repository: UpdateCacheRepository) -> None:
    cache = await repository.get()
    if cache is None:
        await repository.set(
            UpdateCache(
                latest_version=version,
                stored_at_timestamp=int(time.time()),
                seen_whats_new_version=version,
            )
        )
    else:
        await repository.set(
            UpdateCache(
                latest_version=cache.latest_version,
                stored_at_timestamp=cache.stored_at_timestamp,
                seen_whats_new_version=version,
            )
        )
