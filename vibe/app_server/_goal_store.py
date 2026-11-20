"""The session's goal, persisted in ``goal.json`` beside the Unified session.

The file is written before a mutation returns, so a success the client or the
model sees is never lost. Mutation receipts make a retried request or a replayed
tool call return the recorded answer without changing the goal twice.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
import secrets
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, JsonValue

from vibe.app_server.models import PublicGoal
from vibe.utils.io import atomic_replace, file_write_lock, read_safe, read_safe_async
from vibe.utils.platform import PRIVATE_DIRECTORY_MODE

__all__ = [
    "GOAL_FILENAME",
    "MAX_GOAL_RECEIPTS",
    "GoalAccounting",
    "GoalStoreError",
    "GoalTurn",
    "UnifiedGoalStore",
    "read_stored_goal",
]

GOAL_FILENAME = "goal.json"
MAX_GOAL_RECEIPTS = 64


class GoalStoreError(RuntimeError):
    pass


class GoalTurn(BaseModel):
    """One turn bound to the goal, charged once when it ends."""

    model_config = ConfigDict(extra="ignore")

    goal_id: str
    objective_revision: int
    tokens: int = 0
    time_ms: int = 0


class GoalAccounting(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # Turns in progress, by turn id.
    turns: dict[str, GoalTurn] = Field(default_factory=dict)
    # The last charged turn ids, so a replayed turn end is not charged twice.
    charged_turn_ids: list[str] = Field(default_factory=list)
    no_progress_streak: int = 0
    failure_streak: int = 0
    continuations: int = 0
    # Milliseconds charged but not yet a whole second of ``time_used_seconds``,
    # carried to the next charge so short turns add up instead of being dropped.
    time_remainder_ms: int = 0


class _Receipt(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key: str
    response: JsonValue


class _SavedGoal(PublicGoal):
    """``PublicGoal`` as read from disk: a newer build's fields are dropped."""

    model_config = ConfigDict(extra="ignore")


def _known_goal_fields(value: object) -> object:
    if not isinstance(value, dict):
        return value
    saved = _SavedGoal.model_validate(value)
    return PublicGoal.model_validate(saved.model_dump())


class _StoredGoalV1(BaseModel):
    # Ignored, not forbidden: a newer build may add fields an older one must keep
    # reading.
    model_config = ConfigDict(extra="ignore")

    format: Literal["vibe.goal/v1"] = "vibe.goal/v1"
    goal: Annotated[PublicGoal | None, BeforeValidator(_known_goal_fields)] = None
    accounting: GoalAccounting = Field(default_factory=GoalAccounting)
    receipts: list[_Receipt] = Field(default_factory=list)


def read_stored_goal(path: Path) -> PublicGoal | None:
    """Best-effort read for listings, which answer without a live store."""
    try:
        if not path.exists():
            return None
        raw = read_safe(path, raise_on_error=True).text
        return _StoredGoalV1.model_validate_json(raw).goal
    except Exception:
        return None


type GoalMutation = Callable[
    [PublicGoal | None, GoalAccounting], tuple[PublicGoal | None, GoalAccounting]
]


class UnifiedGoalStore:
    """The one goal of one session, with its accounting and receipts."""

    def __init__(self, path: Path, *, persistent: Callable[[], bool]) -> None:
        self._path = path
        self._persistent = persistent
        self._stored = _StoredGoalV1()
        self._lock = asyncio.Lock()
        self._loaded = False
        # Accounting changed in memory since the last write.
        self._unsaved = False

    @property
    def goal(self) -> PublicGoal | None:
        """Lock-free read: mutations replace the stored document wholesale."""
        return self._stored.goal

    @property
    def accounting(self) -> GoalAccounting:
        return self._stored.accounting.model_copy(deep=True)

    async def restore(self) -> None:
        if not self._path.exists():
            async with self._lock:
                self._loaded = True
            return
        try:
            raw = (await read_safe_async(self._path, raise_on_error=True)).text
            stored = _StoredGoalV1.model_validate_json(raw)
        except Exception as exc:
            raise GoalStoreError(
                f"Failed to read the goal at {self._path}: {exc}"
            ) from exc
        async with self._lock:
            self._stored = stored
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
                raise GoalStoreError(
                    f"Failed to preserve the corrupt goal at {self._path}: {exc}"
                ) from exc
            self._stored = _StoredGoalV1()
            self._loaded = True
            return quarantine_path

    async def mutate[ResultT](
        self,
        mutation: GoalMutation,
        *,
        receipt_key: str | None = None,
        respond: Callable[[PublicGoal | None], ResultT],
        encode: Callable[[ResultT], JsonValue],
        decode: Callable[[JsonValue], ResultT],
        accounting_only: bool = False,
    ) -> ResultT:
        """Apply ``mutation`` and persist it before returning ``respond``'s answer.

        A ``receipt_key`` already seen returns its recorded answer unchanged.
        With ``accounting_only``, a change that leaves the goal as it is stays
        in memory until the next write: running turn counts move with every
        streamed chunk, and the file is written when the turn ends.
        """
        async with self._lock:
            if receipt_key is not None:
                for receipt in self._stored.receipts:
                    if receipt.key == receipt_key:
                        return decode(receipt.response)
            goal, accounting = mutation(
                self._stored.goal, self._stored.accounting.model_copy(deep=True)
            )
            result = respond(goal)
            receipts = self._stored.receipts
            if receipt_key is not None:
                receipts = [
                    *receipts,
                    _Receipt(key=receipt_key, response=encode(result)),
                ][-MAX_GOAL_RECEIPTS:]
            stored = self._stored.model_copy(
                update={"goal": goal, "accounting": accounting, "receipts": receipts}
            )
            if stored == self._stored:
                return result
            if accounting_only and stored.goal == self._stored.goal:
                self._stored = stored
                self._unsaved = True
                return result
            await self._commit(stored)
            return result

    async def update(
        self, mutation: GoalMutation, *, accounting_only: bool = False
    ) -> PublicGoal | None:
        """Apply an internal mutation, without a receipt."""
        return await self.mutate(
            mutation,
            respond=lambda goal: goal,
            encode=lambda _goal: None,
            decode=lambda _value: None,
            accounting_only=accounting_only,
        )

    async def replace(
        self, goal: PublicGoal | None, accounting: GoalAccounting | None = None
    ) -> None:
        """Install a goal carried over from another session (a rewind)."""
        async with self._lock:
            await self._commit(
                _StoredGoalV1(goal=goal, accounting=accounting or GoalAccounting())
            )

    async def persist(self) -> None:
        async with self._lock:
            if self._loaded:
                await self._persist(self._stored)
                self._unsaved = False

    async def flush(self) -> None:
        """Write the accounting kept in memory, if any."""
        async with self._lock:
            if self._unsaved:
                await self._commit(self._stored)

    async def _commit(self, stored: _StoredGoalV1) -> None:
        await self._persist(stored)
        self._stored = stored
        self._loaded = True
        self._unsaved = False

    async def _persist(self, stored: _StoredGoalV1) -> None:
        if not self._persistent():
            return
        try:
            if stored.goal is None and not stored.receipts:
                await asyncio.to_thread(self._path.unlink, missing_ok=True)
                return
            self._path.parent.mkdir(
                parents=True, mode=PRIVATE_DIRECTORY_MODE, exist_ok=True
            )
            # The objective is session content, so the file is owner-only.
            self._path.touch(mode=0o600, exist_ok=True)
            payload = stored.model_dump_json(indent=2, exclude_none=True) + "\n"
            async with file_write_lock(self._path):
                await atomic_replace(self._path, payload)
        except Exception as exc:
            raise GoalStoreError(
                f"Failed to persist the goal at {self._path}: {exc}"
            ) from exc
