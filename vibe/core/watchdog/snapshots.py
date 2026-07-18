from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from vibe.core.types import LLMMessage
from vibe.core.utils.io import read_safe
from vibe.core.watchdog.models import RunState


class SnapshotError(Exception):
    pass


class SnapshotNotFoundError(SnapshotError):
    pass


class ConversationSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{7}$")
    label: str | None = None
    created_at: str
    session_id: str
    messages: tuple[LLMMessage, ...]
    watchdog_state: RunState

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @property
    def display_name(self) -> str:
        return self.label or self.snapshot_id


class ConversationSnapshotStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        label: str | None,
        session_id: str,
        messages: list[LLMMessage],
        watchdog_state: RunState,
    ) -> ConversationSnapshot:
        async with self._lock:
            return await asyncio.to_thread(
                self._create_sync,
                label=label,
                session_id=session_id,
                messages=messages,
                watchdog_state=watchdog_state,
            )

    async def list(self) -> list[ConversationSnapshot]:
        async with self._lock:
            return await asyncio.to_thread(self._list_sync)

    async def resolve(self, reference: str) -> ConversationSnapshot:
        snapshots = await self.list()
        return self._resolve_from(snapshots, reference)

    async def drop(self, reference: str) -> ConversationSnapshot:
        async with self._lock:
            snapshots = await asyncio.to_thread(self._list_sync)
            snapshot = self._resolve_from(snapshots, reference)
            await asyncio.to_thread(self._path(snapshot.snapshot_id).unlink)
            return snapshot

    async def clear(self) -> int:
        async with self._lock:
            return await asyncio.to_thread(self._clear_sync)

    def _create_sync(
        self,
        *,
        label: str | None,
        session_id: str,
        messages: list[LLMMessage],
        watchdog_state: RunState,
    ) -> ConversationSnapshot:
        self.root.mkdir(parents=True, exist_ok=True)
        snapshot_id = self._new_id()
        snapshot = ConversationSnapshot(
            snapshot_id=snapshot_id,
            label=label,
            created_at=datetime.now(UTC).isoformat(),
            session_id=session_id,
            messages=tuple(messages),
            watchdog_state=watchdog_state,
        )
        self._write_atomic(snapshot)
        return snapshot

    def _list_sync(self) -> list[ConversationSnapshot]:
        if not self.root.exists():
            return []
        snapshots: list[ConversationSnapshot] = []
        try:
            for path in self.root.glob("*.json"):
                snapshots.append(
                    ConversationSnapshot.model_validate_json(
                        read_safe(path, raise_on_error=True).text
                    )
                )
        except (OSError, UnicodeError, ValueError) as error:
            raise SnapshotError("Failed to read Watchdog snapshots.") from error
        return sorted(snapshots, key=lambda item: item.created_at, reverse=True)

    def _clear_sync(self) -> int:
        if not self.root.exists():
            return 0
        paths = list(self.root.glob("*.json"))
        try:
            for path in paths:
                path.unlink()
        except OSError as error:
            raise SnapshotError("Failed to clear Watchdog snapshots.") from error
        return len(paths)

    @staticmethod
    def _resolve_from(
        snapshots: list[ConversationSnapshot], reference: str
    ) -> ConversationSnapshot:
        normalized = reference.strip()
        for snapshot in snapshots:
            if snapshot.snapshot_id == normalized:
                return snapshot
        if normalized.isdigit():
            index = int(normalized)
            if index < len(snapshots):
                return snapshots[index]
        raise SnapshotNotFoundError(f"Snapshot `{reference}` was not found.")

    def _new_id(self) -> str:
        while True:
            candidate = uuid4().hex[:7]
            if not self._path(candidate).exists():
                return candidate

    def _path(self, snapshot_id: str) -> Path:
        return self.root / f"{snapshot_id}.json"

    def _write_atomic(self, snapshot: ConversationSnapshot) -> None:
        target = self._path(snapshot.snapshot_id)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.root,
                prefix=".snapshot.",
                suffix=".tmp",
                delete=False,
            ) as temp:
                temp_path = Path(temp.name)
                temp.write(snapshot.model_dump_json())
                temp.flush()
                os.fsync(temp.fileno())
            os.replace(temp_path, target)
        except OSError as error:
            raise SnapshotError("Failed to persist Watchdog snapshot.") from error
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
