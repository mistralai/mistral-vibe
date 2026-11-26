"""The walks and the deletes run in the background worker; the served check runs here,
with the session's credentials. The job set is imported, not only named, so a frozen
build bundles the module the worker loads.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from vibe.app_server._background_worker import BackgroundWorker
from vibe.app_server._skill_store_jobs import skill_store_jobs
from vibe.core.skills.registry import still_served

if TYPE_CHECKING:
    from vibe.core.config import VibeConfigSchema
    from vibe.core.platform_auth import PlatformCredentialFactory

_JOBS = f"{skill_store_jobs.__module__}:{skill_store_jobs.__name__}"


class SkillStoreCleanup:
    def __init__(self, worker: BackgroundWorker) -> None:
        self._worker = worker
        worker.add_jobs(_JOBS)

    async def run(
        self,
        config: VibeConfigSchema,
        roots: Sequence[Path],
        *,
        credentials: PlatformCredentialFactory | None,
    ) -> list[tuple[str, int]]:
        """A machine runs one cleanup a day: a call within a day of the last evicts
        nothing.
        """
        unused = await self._worker.run(
            "skill_store.candidates", {"roots": [str(root) for root in roots]}
        )
        if not unused:
            return []
        served = await still_served(config, unused, credentials=credentials)
        if not any(served.values()):
            return []
        evicted = await self._worker.run("skill_store.evict", {"versions": served})
        return [(skill_id, version) for skill_id, version in evicted]


__all__ = ["SkillStoreCleanup"]
