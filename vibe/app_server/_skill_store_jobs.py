"""The walks and deletes hold the GIL long enough to stall a multiplexed app server's
sessions, so they run in the worker process (ADR 0019); the served check needs the
session's credentials and stays in the app server.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from vibe.app_server._background_worker_process import Job, JobSet
from vibe.core.skills.registry import cleanup_candidates, evict_versions


def skill_store_jobs() -> JobSet:
    def candidates(args: dict[str, Any]) -> dict[str, list[int]]:
        return asyncio.run(cleanup_candidates([Path(root) for root in args["roots"]]))

    def evict(args: dict[str, Any]) -> list[list[str | int]]:
        versions: dict[str, list[int]] = args["versions"]
        return [[skill_id, version] for skill_id, version in evict_versions(versions)]

    return {"skill_store.candidates": Job(candidates), "skill_store.evict": Job(evict)}


__all__ = ["skill_store_jobs"]
