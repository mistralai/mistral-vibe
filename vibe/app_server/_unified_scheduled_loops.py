from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
import math
from pathlib import Path
import secrets
import time
from typing import Literal

from croniter import CroniterError, croniter
from dateutil.tz import gettz
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from vibe.core.loop import (
    MAX_LOOPS_PER_SESSION,
    MIN_INTERVAL_SECONDS,
    LoopError,
    parse_interval,
)
from vibe.utils.io import atomic_replace, file_write_lock, read_safe_async


class ScheduledLoopStoreError(RuntimeError):
    pass


_CRON_FIELD_COUNT = 5


def _next_cron_fire(cron: str, now: float) -> float:
    if len(cron.split()) != _CRON_FIELD_COUNT:
        raise ValueError("Cron must be a standard 5-field expression.")
    try:
        timezone = gettz()
        if timezone is None:
            raise ValueError("Cannot determine the machine local timezone.")
        # Keep transition rules so croniter can traverse repeated and missing hours.
        return (
            croniter(
                cron,
                datetime.fromtimestamp(now, tz=timezone),
                max_years_between_matches=50,
            )
            .get_next(datetime)
            .timestamp()
        )
    except (CroniterError, ValueError, OverflowError, OSError) as exc:
        raise ValueError(f"Invalid cron expression `{cron}`: {exc}") from exc


class ScheduledPrompt(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    id: str
    interval_seconds: int | None = Field(
        default=None, strict=True, ge=MIN_INTERVAL_SECONDS
    )
    prompt: str
    next_fire_at: float = Field(allow_inf_nan=False)
    created_at: float = Field(allow_inf_nan=False)
    cron: str | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, prompt: str) -> str:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("Missing prompt.")
        if prompt.startswith("/"):
            raise ValueError("Prompt cannot start with '/'.")
        return prompt

    @model_validator(mode="after")
    def validate_schedule(self) -> ScheduledPrompt:
        if (self.interval_seconds is None) == (self.cron is None):
            raise ValueError("Exactly one of interval_seconds or cron is required.")
        if self.cron is not None:
            self.cron = self.cron.strip()
            _next_cron_fire(self.cron, self.created_at)
        return self

    def next_fire_after(self, now: float) -> float:
        if self.interval_seconds is not None:
            return now + self.interval_seconds
        assert self.cron is not None
        return _next_cron_fire(self.cron, now)


class _StoredLoopsV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["vibe.scheduled-loops/v1"] = "vibe.scheduled-loops/v1"
    loops: list[ScheduledPrompt] = Field(max_length=MAX_LOOPS_PER_SESSION)


class UnifiedScheduledLoops:
    """Vibe-owned loop schedule kept beside one Unified Harness session."""

    def __init__(self, path: Path, *, persistent: Callable[[], bool]) -> None:
        self._path = path
        self._persistent = persistent
        self._loops: list[ScheduledPrompt] = []
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

    async def replace(self, loops: Sequence[ScheduledPrompt]) -> None:
        async with self._lock:
            await self._commit(loops)

    async def list(self) -> list[ScheduledPrompt]:
        async with self._lock:
            return [loop.model_copy(deep=True) for loop in self._loops]

    async def create(self, interval: str, prompt: str) -> ScheduledPrompt:
        return await self.create_interval(parse_interval(interval), prompt)

    async def create_interval(
        self, interval_seconds: int, prompt: str
    ) -> ScheduledPrompt:
        return await self._create(prompt, interval_seconds=interval_seconds)

    async def create_cron(self, cron: str, prompt: str) -> ScheduledPrompt:
        return await self._create(prompt, cron=cron)

    async def _create(
        self,
        prompt: str,
        *,
        interval_seconds: int | None = None,
        cron: str | None = None,
    ) -> ScheduledPrompt:
        async with self._lock:
            if len(self._loops) >= MAX_LOOPS_PER_SESSION:
                raise LoopError(
                    f"Loop limit reached ({MAX_LOOPS_PER_SESSION} per session)."
                )
            now = time.time()
            try:
                loop = ScheduledPrompt(
                    id=secrets.token_hex(4),
                    interval_seconds=interval_seconds,
                    cron=cron,
                    prompt=prompt,
                    next_fire_at=now,
                    created_at=now,
                )
                loop.next_fire_at = loop.next_fire_after(now)
            except (ValueError, OverflowError) as exc:
                raise LoopError(str(exc)) from exc
            await self._commit([*self._loops, loop])
            return loop.model_copy(deep=True)

    async def delete(self, loop_id: str) -> ScheduledPrompt:
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

    async def due(self, now: float | None = None) -> ScheduledPrompt | None:
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
    ) -> ScheduledPrompt | None:
        async with self._lock:
            rescheduled = self._rescheduled(loop_id, now)
            if rescheduled is None:
                return None
            await self._commit([
                rescheduled if item.id == loop_id else item for item in self._loops
            ])
            return rescheduled.model_copy(deep=True)

    async def defer(self, loop_id: str, now: float | None = None) -> None:
        """Advance a loop to its next occurrence in memory only.

        Used when `mark_fired` cannot persist: the loop stays due on disk, but it
        must not keep firing this session while other schedules wait their turn.
        """
        async with self._lock:
            rescheduled = self._rescheduled(loop_id, now)
            if rescheduled is not None:
                self._loops = [
                    rescheduled if item.id == loop_id else item for item in self._loops
                ]

    def _rescheduled(self, loop_id: str, now: float | None) -> ScheduledPrompt | None:
        loop = next((item for item in self._loops if item.id == loop_id), None)
        if loop is None:
            return None
        return loop.model_copy(
            update={
                "next_fire_at": loop.next_fire_after(
                    now if now is not None else time.time()
                )
            },
            deep=True,
        )

    async def persist(self) -> None:
        async with self._lock:
            if self._loaded:
                await self._persist(_StoredLoopsV1(loops=self._loops))

    async def _commit(self, loops: Sequence[ScheduledPrompt]) -> None:
        candidate = _StoredLoopsV1(loops=list(loops))
        await self._persist(candidate)
        self._loops = candidate.loops
        self._loaded = True

    async def _persist(self, stored: _StoredLoopsV1) -> None:
        if not self._persistent():
            return
        try:
            self._path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            # Scheduled loops reference session content, so the store file is
            # created owner-only; an existing file keeps its current mode.
            self._path.touch(mode=0o600, exist_ok=True)
            payload = stored.model_dump_json(indent=2, exclude_none=True) + "\n"
            async with file_write_lock(self._path):
                await atomic_replace(self._path, payload)
        except Exception as exc:
            raise ScheduledLoopStoreError(
                f"Failed to persist scheduled loops at {self._path}: {exc}"
            ) from exc
