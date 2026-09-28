"""Bounded Host-scoped persistent descriptor cache for Unified MCP."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
)

from mistralai_vibe_local_harness.vibe._mcp_models import (
    JsonSchema,
    MCPDescriptorCacheKey,
    MCPDescriptorCachePolicy,
    MCPDescriptorCacheRecordV1,
    MCPRemoteToolDescriptor,
)

_JSON_SCHEMA = TypeAdapter(JsonSchema)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _CacheKeyV1(_StrictModel):
    format_version: Literal[1] = Field(alias="formatVersion")
    naming_version: str = Field(alias="namingVersion", min_length=1)
    server_fingerprint: str = Field(alias="serverFingerprint", min_length=1)
    authorization_descriptor_revision: str = Field(
        alias="authorizationDescriptorRevision", min_length=1
    )


class _DescriptorV1(_StrictModel):
    remote_name: str = Field(alias="remoteName", min_length=1)
    description: str = ""
    input_schema: JsonValue = Field(alias="inputSchema", default_factory=dict)
    output_schema: JsonValue = Field(alias="outputSchema", default=None)
    annotations: JsonValue = None


class _CacheRecordV1(_StrictModel):
    key: _CacheKeyV1
    source_name: str = Field(alias="sourceName", min_length=1)
    discovered_at: datetime = Field(alias="discoveredAt")
    last_used_at: datetime = Field(alias="lastUsedAt")
    descriptors: list[_DescriptorV1]

    def to_runtime(self) -> MCPDescriptorCacheRecordV1:
        return MCPDescriptorCacheRecordV1(
            key=MCPDescriptorCacheKey(
                format_version=self.key.format_version,
                naming_version=self.key.naming_version,
                server_fingerprint=self.key.server_fingerprint,
                authorization_descriptor_revision=(
                    self.key.authorization_descriptor_revision
                ),
            ),
            source_name=self.source_name,
            discovered_at=self.discovered_at,
            last_used_at=self.last_used_at,
            descriptors=tuple(
                MCPRemoteToolDescriptor(
                    remote_name=descriptor.remote_name,
                    description=descriptor.description,
                    input_schema=_JSON_SCHEMA.validate_python(descriptor.input_schema),
                    output_schema=(
                        None
                        if descriptor.output_schema is None
                        else _JSON_SCHEMA.validate_python(descriptor.output_schema)
                    ),
                    annotations=descriptor.annotations,
                )
                for descriptor in self.descriptors
            ),
        )

    @classmethod
    def from_runtime(cls, record: MCPDescriptorCacheRecordV1) -> Self:
        return cls.model_validate({
            "key": {
                "formatVersion": record.key.format_version,
                "namingVersion": record.key.naming_version,
                "serverFingerprint": record.key.server_fingerprint,
                "authorizationDescriptorRevision": (
                    record.key.authorization_descriptor_revision
                ),
            },
            "sourceName": record.source_name,
            "discoveredAt": record.discovered_at,
            "lastUsedAt": record.last_used_at,
            "descriptors": [
                {
                    "remoteName": descriptor.remote_name,
                    "description": descriptor.description,
                    "inputSchema": descriptor.input_schema,
                    "outputSchema": descriptor.output_schema,
                    "annotations": descriptor.annotations,
                }
                for descriptor in record.descriptors
            ],
        })


class MCPDescriptorCache:
    def __init__(self, root: Path, policy: MCPDescriptorCachePolicy) -> None:
        _validate_policy(policy)
        self._root = root
        self._policy = policy
        self._lock = threading.RLock()

    async def read(
        self,
        key: MCPDescriptorCacheKey,
        *,
        source_name: str,
        now: datetime | None = None,
    ) -> MCPDescriptorCacheRecordV1 | None:
        return await asyncio.to_thread(self._read_sync, key, source_name, now)

    async def write(
        self,
        key: MCPDescriptorCacheKey,
        *,
        source_name: str,
        descriptors: tuple[MCPRemoteToolDescriptor, ...],
        now: datetime | None = None,
    ) -> bool:
        return await asyncio.to_thread(
            self._write_sync,
            MCPDescriptorCacheRecordV1(
                key=key,
                source_name=source_name,
                discovered_at=now or _utc_now(),
                last_used_at=now or _utc_now(),
                descriptors=descriptors,
            ),
        )

    async def invalidate(self, key: MCPDescriptorCacheKey) -> None:
        await asyncio.to_thread(self._invalidate_sync, key)

    async def touch(
        self,
        key: MCPDescriptorCacheKey,
        *,
        source_name: str,
        discovered_at: datetime,
        now: datetime | None = None,
    ) -> None:
        await asyncio.to_thread(self._touch_sync, key, source_name, discovered_at, now)

    def _read_sync(  # noqa: PLR0911 - one return per cache-miss reason
        self, key: MCPDescriptorCacheKey, source_name: str, now: datetime | None
    ) -> MCPDescriptorCacheRecordV1 | None:
        if self._policy.ttl_s == 0:
            return None
        with self._lock:
            now = now or _utc_now()
            path = self._path(key)
            try:
                if path.stat().st_size > self._policy.max_record_bytes:
                    return None
                payload = path.read_bytes()
                record = _parse_record(payload)
            except (OSError, ValidationError, ValueError, TypeError):
                return None
            runtime = record.to_runtime()
            if runtime.key != key or runtime.source_name != source_name:
                return None
            if len(runtime.descriptors) > self._policy.max_tools_per_record:
                return None
            if not _is_valid_timestamp(runtime.discovered_at, now):
                return None
            if not _is_valid_timestamp(runtime.last_used_at, now):
                return None
            age_s = (now - runtime.discovered_at).total_seconds()
            if age_s >= self._policy.ttl_s:
                return None
            touched = MCPDescriptorCacheRecordV1(
                key=runtime.key,
                source_name=runtime.source_name,
                discovered_at=runtime.discovered_at,
                last_used_at=now,
                descriptors=runtime.descriptors,
            )
            self._best_effort_replace(path, _encode_record(touched))
            return touched

    def _write_sync(self, record: MCPDescriptorCacheRecordV1) -> bool:  # noqa: PLR0911 - one return per rejection reason
        if self._policy.ttl_s == 0:
            return False
        if len(record.descriptors) > self._policy.max_tools_per_record:
            return False
        if not _is_aware(record.discovered_at) or not _is_aware(record.last_used_at):
            return False
        try:
            payload = _encode_record(record)
        except (ValidationError, ValueError, TypeError):
            return False
        if len(payload) > self._policy.max_record_bytes:
            return False
        with self._lock:
            try:
                self._ensure_root()
                self._replace(self._path(record.key), payload)
                self._evict()
            except OSError:
                return False
        return True

    def _invalidate_sync(self, key: MCPDescriptorCacheKey) -> None:
        with self._lock:
            try:
                self._path(key).unlink(missing_ok=True)
            except OSError:
                return

    def _touch_sync(
        self,
        key: MCPDescriptorCacheKey,
        source_name: str,
        discovered_at: datetime,
        now: datetime | None,
    ) -> None:
        if self._policy.ttl_s == 0:
            return
        with self._lock:
            now = now or _utc_now()
            path = self._path(key)
            try:
                if path.stat().st_size > self._policy.max_record_bytes:
                    return
                runtime = _parse_record(path.read_bytes()).to_runtime()
                if (
                    runtime.key != key
                    or runtime.source_name != source_name
                    or runtime.discovered_at != discovered_at
                    or not _is_valid_timestamp(discovered_at, now)
                ):
                    return
                touched = MCPDescriptorCacheRecordV1(
                    key=runtime.key,
                    source_name=runtime.source_name,
                    discovered_at=runtime.discovered_at,
                    last_used_at=now,
                    descriptors=runtime.descriptors,
                )
                self._best_effort_replace(path, _encode_record(touched))
            except (OSError, ValidationError, ValueError, TypeError):
                return

    def _path(self, key: MCPDescriptorCacheKey) -> Path:
        raw = json.dumps(
            {
                "formatVersion": key.format_version,
                "namingVersion": key.naming_version,
                "serverFingerprint": key.server_fingerprint,
                "authorizationDescriptorRevision": (
                    key.authorization_descriptor_revision
                ),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return self._root / f"{hashlib.sha256(raw.encode()).hexdigest()}.json"

    def _ensure_root(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with _suppress_os_error():
            self._root.chmod(0o700)

    def _replace(self, path: Path, payload: bytes) -> None:
        file_descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=self._root
        )
        temporary_path = Path(temporary)
        try:
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                try:
                    fchmod(file_descriptor, 0o600)
                except OSError:
                    pass
            with os.fdopen(file_descriptor, "wb", closefd=True) as output:
                output.write(payload)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, path)
            with _suppress_os_error():
                path.chmod(0o600)
        except BaseException:
            with _suppress_os_error():
                os.close(file_descriptor)
            with _suppress_os_error():
                temporary_path.unlink(missing_ok=True)
            raise

    def _best_effort_replace(self, path: Path, payload: bytes) -> None:
        try:
            self._ensure_root()
            self._replace(path, payload)
        except OSError:
            return

    def _evict(self) -> None:
        files = [
            path
            for path in self._root.glob("*.json")
            if path.is_file() and not path.name.startswith(".")
        ]
        entries: list[tuple[datetime, Path, int]] = []
        for path in files:
            try:
                size = path.stat().st_size
                record = _parse_record(path.read_bytes())
                last_used = record.last_used_at
                if not _is_aware(last_used):
                    raise ValueError("naive timestamp")
            except (OSError, ValidationError, ValueError, TypeError):
                with _suppress_os_error():
                    path.unlink(missing_ok=True)
                continue
            entries.append((last_used, path, size))
        entries.sort(key=lambda entry: (entry[0], entry[1].name))
        total_bytes = sum(entry[2] for entry in entries)
        while (
            len(entries) > self._policy.max_files
            or total_bytes > self._policy.max_directory_bytes
        ):
            _, path, size = entries.pop(0)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                break
            total_bytes -= size


class _suppress_os_error:
    def __enter__(self) -> None:
        return None

    def __exit__(
        self, exception_type: object, exception: object, traceback: object
    ) -> bool:
        return isinstance(exception, OSError)


def _parse_record(payload: bytes) -> _CacheRecordV1:
    return _CacheRecordV1.model_validate_json(payload, strict=True)


def _encode_record(record: MCPDescriptorCacheRecordV1) -> bytes:
    model = _CacheRecordV1.from_runtime(record)
    return model.model_dump_json(by_alias=True, exclude_none=False).encode("utf-8")


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _is_valid_timestamp(value: datetime, now: datetime) -> bool:
    return _is_aware(value) and value <= now


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_policy(policy: MCPDescriptorCachePolicy) -> None:
    if not math.isfinite(policy.ttl_s) or policy.ttl_s < 0:
        raise ValueError("descriptor cache TTL must be finite and non-negative")
    for name in (
        "max_tools_per_record",
        "max_record_bytes",
        "max_files",
        "max_directory_bytes",
    ):
        if getattr(policy, name) <= 0:
            raise ValueError(f"descriptor cache {name} must be positive")


__all__ = ["MCPDescriptorCache"]
