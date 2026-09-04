from __future__ import annotations

import asyncio
from collections.abc import Callable
import math
from pathlib import Path
import secrets
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict

from vibe.core.loop import MAX_LOOPS_PER_SESSION, LoopError, parse_interval
from vibe.core.types import ScheduledLoop
from vibe.utils.io import atomic_replace, file_write_lock, read_safe_async


class ScheduledLoopStoreError(RuntimeError):
    pass


class _StoredLoopsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["vibe.scheduled-loops/v1"] = "vibe.scheduled-loops/v1"
    loops: list[ScheduledLoop]


class UnifiedScheduledLoops:
    """Vibe-owned loop schedule kept beside one Unified Harness session."""

    def __init__(self, path: Path, *, persistent: Callable[[], bool]) -> None:
        self._path = path
        self._persistent = persistent
        self._loops: list[ScheduledLoop] = []
        self._lock = asyncio.Lock()
        self._loaded = False

    async def restore(self) -> None:
        if not self._path.exists():
            async with self._lock:
                self._loaded = True
            return
        try:
            raw = (await read_safe_async(self._path, raise_on_error=True)).text
            stored = _StoredLoopsV1.model_validate_json(raw)
        except Exception as exc:
            raise ScheduledLoopStoreError(
                f"Failed to read scheduled loops at {self._path}: {exc}"
            ) from exc
        async with self._lock:
            self._loops = [loop.model_copy(deep=True) for loop in stored.loops]
            self._loaded = True

    async def quarantine_corrupt_store(self) -> Path:
        async with self._lock:
            quarantine_path = self._path.with_name(
                f"{self._path.stem}.corrupt-{secrets.token_hex(4)}{self._path.suffix}"
            )
            try:
                async with file_write_lock(self._path):
                    await asyncio.to_thread(self._path.replace, quarantine_path)
            except Exception as exc:
                raise ScheduledLoopStoreError(
                    f"Failed to preserve corrupt scheduled loops at {self._path}: {exc}"
                ) from exc
            self._loops = []
            self._loaded = True
            return quarantine_path

    async def replace(self, loops: list[ScheduledLoop]) -> None:
        async with self._lock:
            await self._commit(loops)

    async def list(self) -> list[ScheduledLoop]:
        async with self._lock:
            return [loop.model_copy(deep=True) for loop in self._loops]

    async def create(self, interval: str, prompt: str) -> ScheduledLoop:
        seconds = parse_interval(interval)
        prompt = prompt.strip()
        if not prompt:
            raise LoopError("Missing prompt.")
        if prompt.startswith("/"):
            raise LoopError("Prompt cannot start with '/'.")
        async with self._lock:
            if len(self._loops) >= MAX_LOOPS_PER_SESSION:
                raise LoopError(
                    f"Loop limit reached ({MAX_LOOPS_PER_SESSION} per session)."
                )
            now = time.time()
            loop = ScheduledLoop(
                id=secrets.token_hex(4),
                interval_seconds=seconds,
                prompt=prompt,
                next_fire_at=now + seconds,
                created_at=now,
            )
            await self._commit([*self._loops, loop])
            return loop.model_copy(deep=True)

    async def delete(self, loop_id: str) -> ScheduledLoop:
        async with self._lock:
            loop = next((item for item in self._loops if item.id == loop_id), None)
            if loop is None:
                raise LoopError(f"No scheduled loop with id `{loop_id}`.")
            await self._commit([item for item in self._loops if item.id != loop_id])
            return loop.model_copy(deep=True)

    async def clear(self) -> int:
        async with self._lock:
            count = len(self._loops)
            await self._commit([])
            return count

    async def next_due_in(self, now: float | None = None) -> float:
        async with self._lock:
            if not self._loops:
                return math.inf
            timestamp = now if now is not None else time.time()
            return max(0.0, min(loop.next_fire_at for loop in self._loops) - timestamp)

    async def due(self, now: float | None = None) -> ScheduledLoop | None:
        async with self._lock:
            timestamp = now if now is not None else time.time()
            loop = min(
                (item for item in self._loops if item.next_fire_at <= timestamp),
                key=lambda item: item.next_fire_at,
                default=None,
            )
            return None if loop is None else loop.model_copy(deep=True)

    async def mark_fired(
        self, loop_id: str, now: float | None = None
    ) -> ScheduledLoop | None:
        async with self._lock:
            loop = next((item for item in self._loops if item.id == loop_id), None)
            if loop is None:
                return None
            rescheduled = loop.model_copy(
                update={
                    "next_fire_at": (now if now is not None else time.time())
                    + loop.interval_seconds
                },
                deep=True,
            )
            await self._commit([
                rescheduled if item.id == loop_id else item for item in self._loops
            ])
            return rescheduled.model_copy(deep=True)

    async def persist(self) -> None:
        async with self._lock:
            if self._loaded:
                await self._persist(self._loops)

    async def _commit(self, loops: list[ScheduledLoop]) -> None:
        candidate = [loop.model_copy(deep=True) for loop in loops]
        await self._persist(candidate)
        self._loops = candidate
        self._loaded = True

    async def _persist(self, loops: list[ScheduledLoop]) -> None:
        if not self._persistent():
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = _StoredLoopsV1(loops=loops).model_dump_json(indent=2) + "\n"
            async with file_write_lock(self._path):
                await atomic_replace(self._path, payload)
        except Exception as exc:
            raise ScheduledLoopStoreError(
                f"Failed to persist scheduled loops at {self._path}: {exc}"
            ) from exc
