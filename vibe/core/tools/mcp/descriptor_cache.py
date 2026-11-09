"""Independent persistent raw-descriptor cache for the legacy MCP registry."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vibe.core.tools.remote import RemoteTool

_FORMAT = "mistral.vibe.legacy-mcp-descriptors/v1"
_MAX_TOOLS = 1_000
_MAX_RECORD_BYTES = 2 * 1024 * 1024
_MAX_FILES = 512
_MAX_DIRECTORY_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class LegacyDescriptorCacheRecord:
    key: str
    source_name: str
    discovered_at: datetime
    last_used_at: datetime
    descriptors: tuple[RemoteTool, ...]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Descriptor(_StrictModel):
    name: str = Field(min_length=1)
    description: str | None = None
    input_schema: dict[str, object] = Field(alias="inputSchema")
    output_schema: dict[str, object] | None = Field(default=None, alias="outputSchema")


class _Record(_StrictModel):
    format: Literal["mistral.vibe.legacy-mcp-descriptors/v1"]
    key: str = Field(min_length=1)
    source_name: str = Field(alias="sourceName", min_length=1)
    discovered_at: datetime = Field(alias="discoveredAt")
    last_used_at: datetime = Field(alias="lastUsedAt")
    descriptors: list[_Descriptor]

    @classmethod
    def from_runtime(cls, record: LegacyDescriptorCacheRecord) -> Self:
        return cls.model_validate({
            "format": _FORMAT,
            "key": record.key,
            "sourceName": record.source_name,
            "discoveredAt": record.discovered_at,
            "lastUsedAt": record.last_used_at,
            "descriptors": [
                {
                    "name": descriptor.name,
                    "description": descriptor.description,
                    "inputSchema": descriptor.input_schema,
                    "outputSchema": descriptor.output_schema,
                }
                for descriptor in record.descriptors
            ],
        })

    def to_runtime(self) -> LegacyDescriptorCacheRecord:
        return LegacyDescriptorCacheRecord(
            key=self.key,
            source_name=self.source_name,
            discovered_at=self.discovered_at,
            last_used_at=self.last_used_at,
            descriptors=tuple(
                # By alias: ``RemoteTool`` takes its schemas under the wire
                # names only, and a read that dropped them would be written
                # back over the entry it just read.
                RemoteTool.model_validate({
                    "name": descriptor.name,
                    "description": descriptor.description,
                    "inputSchema": descriptor.input_schema,
                    "outputSchema": descriptor.output_schema,
                })
                for descriptor in self.descriptors
            ),
        )


class LegacyMCPDescriptorCache:
    def __init__(self, root: Path, *, ttl_s: float = 86_400.0) -> None:
        if not math.isfinite(ttl_s) or ttl_s < 0:
            raise ValueError("descriptor cache TTL must be finite and non-negative")
        self._root = root
        self._ttl_s = ttl_s
        self._lock = threading.RLock()

    async def read(
        self, key: str, *, source_name: str, now: datetime | None = None
    ) -> LegacyDescriptorCacheRecord | None:
        return await asyncio.to_thread(self._read_sync, key, source_name, now)

    async def write(
        self,
        key: str,
        *,
        source_name: str,
        descriptors: tuple[RemoteTool, ...],
        now: datetime | None = None,
    ) -> bool:
        timestamp = now or _utc_now()
        return await asyncio.to_thread(
            self._write_sync,
            LegacyDescriptorCacheRecord(
                key=key,
                source_name=source_name,
                discovered_at=timestamp,
                last_used_at=timestamp,
                descriptors=descriptors,
            ),
        )

    async def touch(
        self,
        key: str,
        *,
        source_name: str,
        discovered_at: datetime,
        now: datetime | None = None,
    ) -> None:
        await asyncio.to_thread(self._touch_sync, key, source_name, discovered_at, now)

    async def invalidate(self, key: str) -> None:
        await asyncio.to_thread(self._invalidate_sync, key)

    def _read_sync(
        self, key: str, source_name: str, now: datetime | None
    ) -> LegacyDescriptorCacheRecord | None:
        if self._ttl_s == 0:
            return None
        with self._lock:
            now = now or _utc_now()
            record = self._load(key)
            if record is None or record.key != key or record.source_name != source_name:
                return None
            if not self._fresh(record, now):
                return None
            touched = LegacyDescriptorCacheRecord(
                key=record.key,
                source_name=record.source_name,
                discovered_at=record.discovered_at,
                last_used_at=now,
                descriptors=record.descriptors,
            )
            self._best_effort_replace(self._path(key), _encode(touched))
            return touched

    def _write_sync(self, record: LegacyDescriptorCacheRecord) -> bool:
        if self._ttl_s == 0 or len(record.descriptors) > _MAX_TOOLS:
            return False
        if not _aware(record.discovered_at) or not _aware(record.last_used_at):
            return False
        try:
            payload = _encode(record)
        except (ValidationError, ValueError, TypeError):
            return False
        if len(payload) > _MAX_RECORD_BYTES:
            return False
        with self._lock:
            try:
                self._ensure_root()
                self._replace(self._path(record.key), payload)
                self._evict()
            except OSError:
                return False
        return True

    def _touch_sync(
        self, key: str, source_name: str, discovered_at: datetime, now: datetime | None
    ) -> None:
        if self._ttl_s == 0:
            return
        with self._lock:
            now = now or _utc_now()
            record = self._load(key)
            if (
                record is None
                or record.source_name != source_name
                or record.discovered_at != discovered_at
                or not self._fresh(record, now)
            ):
                return
            touched = LegacyDescriptorCacheRecord(
                key=record.key,
                source_name=record.source_name,
                discovered_at=record.discovered_at,
                last_used_at=now,
                descriptors=record.descriptors,
            )
            self._best_effort_replace(self._path(key), _encode(touched))

    def _invalidate_sync(self, key: str) -> None:
        with self._lock:
            try:
                self._path(key).unlink(missing_ok=True)
            except OSError:
                return

    def _load(self, key: str) -> LegacyDescriptorCacheRecord | None:
        path = self._path(key)
        try:
            if path.stat().st_size > _MAX_RECORD_BYTES:
                return None
            record = _Record.model_validate_json(path.read_bytes(), strict=True)
            if len(record.descriptors) > _MAX_TOOLS:
                return None
            return record.to_runtime()
        except (OSError, ValidationError, ValueError, TypeError):
            return None

    def _fresh(self, record: LegacyDescriptorCacheRecord, now: datetime) -> bool:
        if not _valid_timestamp(record.discovered_at, now):
            return False
        if not _valid_timestamp(record.last_used_at, now):
            return False
        return (now - record.discovered_at).total_seconds() < self._ttl_s

    def _path(self, key: str) -> Path:
        return self._root / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self._root.chmod(0o700)
        except OSError:
            pass

    def _replace(self, path: Path, payload: bytes) -> None:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=self._root
        )
        temporary_path = Path(temporary)
        try:
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                try:
                    fchmod(descriptor, 0o600)
                except OSError:
                    pass
            with os.fdopen(descriptor, "wb", closefd=True) as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        except BaseException:
            try:
                os.close(descriptor)
            except OSError:
                pass
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _best_effort_replace(self, path: Path, payload: bytes) -> None:
        try:
            self._ensure_root()
            self._replace(path, payload)
        except OSError:
            return

    def _evict(self) -> None:
        entries: list[tuple[datetime, Path, int]] = []
        for path in self._root.glob("*.json"):
            try:
                size = path.stat().st_size
                record = _Record.model_validate_json(path.read_bytes(), strict=True)
                if not _aware(record.last_used_at):
                    raise ValueError("naive timestamp")
            except (OSError, ValidationError, ValueError, TypeError):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            entries.append((record.last_used_at, path, size))
        entries.sort(key=lambda entry: (entry[0], entry[1].name))
        total = sum(entry[2] for entry in entries)
        while len(entries) > _MAX_FILES or total > _MAX_DIRECTORY_BYTES:
            _, path, size = entries.pop(0)
            path.unlink(missing_ok=True)
            total -= size


def descriptor_cache_key(*, server_fingerprint: str, descriptor_revision: str) -> str:
    return json.dumps(
        {
            "format": _FORMAT,
            "namingVersion": "legacy-proxy/v1",
            "serverFingerprint": server_fingerprint,
            "descriptorRevision": descriptor_revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _encode(record: LegacyDescriptorCacheRecord) -> bytes:
    encoded = _Record.from_runtime(record)
    return encoded.model_dump_json(by_alias=True, exclude_none=False).encode()


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _valid_timestamp(value: datetime, now: datetime) -> bool:
    return _aware(value) and value <= now


def _utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = [
    "LegacyDescriptorCacheRecord",
    "LegacyMCPDescriptorCache",
    "descriptor_cache_key",
]
