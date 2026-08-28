from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import TYPE_CHECKING, Any, TypedDict

from vibe.observability.logging import logger
from vibe.utils.io import read_safe

if TYPE_CHECKING:
    from vibe.core.config import SessionLoggingConfig

INDEX_FILENAME = ".session_index.json"
METADATA_FILENAME = "meta.json"
MESSAGES_FILENAME = "messages.jsonl"


class SessionInfo(TypedDict):
    session_id: str
    cwd: str
    # Where the session started, when that differs from where it sits now.
    # The listing has to offer a moved session from both, so one cannot do.
    origin_directory: str | None
    parent_session_id: str | None
    title: str | None
    start_time: str | None
    end_time: str | None
    updated_at: str


@dataclass
class _Entry:
    mtime_ns: int
    info: SessionInfo


def _convert_to_utc_iso(date_str: str) -> str:
    """Normalize an ISO timestamp to UTC, assuming local time when naive."""
    dt = datetime.fromisoformat(date_str)
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(UTC).isoformat()


def _mtime_to_utc_iso(mtime_ns: int) -> str:
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000, UTC).isoformat()


def _build_info(
    metadata: dict[str, Any], fallback_updated_at: str
) -> SessionInfo | None:
    """Build a listing entry from raw meta.json, or None if it lacks a session id."""
    session_id = metadata.get("session_id")
    if not session_id:
        return None

    environment = metadata.get("environment")
    session_cwd = ""
    if isinstance(environment, dict):
        session_cwd = environment.get("working_directory") or ""

    end_time = metadata.get("end_time")
    if end_time:
        try:
            end_time = _convert_to_utc_iso(end_time)
        except (ValueError, OSError):
            end_time = None
    start_time = metadata.get("start_time")
    if start_time:
        try:
            start_time = _convert_to_utc_iso(start_time)
        except (ValueError, OSError):
            start_time = None

    origin_directory = metadata.get("origin_directory")

    return {
        "session_id": session_id,
        "cwd": session_cwd,
        "origin_directory": (
            origin_directory if isinstance(origin_directory, str) else None
        ),
        "parent_session_id": metadata.get("parent_session_id"),
        "title": metadata.get("title"),
        "start_time": start_time,
        "end_time": end_time,
        "updated_at": end_time or start_time or fallback_updated_at,
    }


def _entry_from_payload(payload: Any) -> _Entry | None:
    """Parse one persisted index record, or None if it is malformed."""
    if not isinstance(payload, dict):
        return None
    mtime_ns = payload.get("mtime_ns")
    session_id = payload.get("session_id")
    if not isinstance(mtime_ns, int) or not isinstance(session_id, str):
        return None
    cwd = payload.get("cwd")
    origin_directory = payload.get("origin_directory")
    info: SessionInfo = {
        "session_id": session_id,
        "cwd": cwd if isinstance(cwd, str) else "",
        "origin_directory": (
            origin_directory if isinstance(origin_directory, str) else None
        ),
        "parent_session_id": payload.get("parent_session_id"),
        "title": payload.get("title"),
        "start_time": payload.get("start_time"),
        "end_time": payload.get("end_time"),
        "updated_at": (
            payload["updated_at"]
            if isinstance(payload.get("updated_at"), str) and payload["updated_at"]
            else _mtime_to_utc_iso(mtime_ns)
        ),
    }
    return _Entry(mtime_ns=mtime_ns, info=info)


class SessionIndex:
    """In-memory, disk-persisted cache of session listing metadata.

    The `meta.json` files remain the source of truth; this index is a cache that
    reconciles against them on every read using cheap `stat` calls and only
    re-reads directories whose `meta.json` changed. Any structural corruption of
    the persisted index triggers a full rebuild from the session directories.
    """

    def __init__(self, save_dir: Path, session_prefix: str) -> None:
        """Bind the index to a session directory without touching disk yet."""
        self._save_dir = save_dir
        self._pattern = f"{session_prefix}_*"
        self._index_path = save_dir / INDEX_FILENAME
        self._lock = threading.Lock()
        self._entries: dict[str, _Entry] = {}
        self._loaded = False

    def list(self, cwd: str | None = None) -> list[SessionInfo]:
        """Return cached session infos, reconciled against disk, filtered by cwd."""
        with self._lock:
            self._reconcile()
            result: list[SessionInfo] = [
                SessionInfo(
                    session_id=entry.info["session_id"],
                    cwd=entry.info["cwd"],
                    origin_directory=entry.info["origin_directory"],
                    parent_session_id=entry.info["parent_session_id"],
                    title=entry.info["title"],
                    start_time=entry.info["start_time"],
                    end_time=entry.info["end_time"],
                    updated_at=entry.info["updated_at"],
                )
                for entry in self._entries.values()
            ]
        if cwd is None:
            return result
        # Either directory, matching SessionLoader._session_reaches.
        # Filtering on the current one alone would drop a moved session
        # from the listing at the directory the user started it in.
        return [
            info for info in result if cwd in {info["cwd"], info["origin_directory"]}
        ]

    def _reconcile(self) -> None:
        """Sync the in-memory cache with the session dirs via cheap stat checks."""
        if not self._loaded:
            self._entries = self._load_persisted()
            self._loaded = True

        if not self._save_dir.exists():
            self._entries = {}
            return

        dirty = False
        seen: set[str] = set()
        for session_dir in self._save_dir.glob(self._pattern):
            meta_path = session_dir / METADATA_FILENAME
            try:
                mtime_ns = meta_path.stat().st_mtime_ns
            except OSError:
                continue
            seen.add(session_dir.name)

            cached = self._entries.get(session_dir.name)
            if cached is not None and cached.mtime_ns == mtime_ns:
                continue

            entry = self._read_entry(session_dir, mtime_ns)
            if entry is None:
                if self._entries.pop(session_dir.name, None) is not None:
                    dirty = True
                # If the file is corrupted, and it is already set to None in our cache
                # then the cache is sync and not dirty
                continue
            self._entries[session_dir.name] = entry
            dirty = True

        for name in list(self._entries):
            if name not in seen:
                del self._entries[name]
                dirty = True

        if dirty:
            self._persist()

    def _read_entry(self, session_dir: Path, mtime_ns: int) -> _Entry | None:
        """Read one session dir into an entry, or None if invalid/unreadable."""
        try:
            msg_size = (session_dir / MESSAGES_FILENAME).stat().st_size
        except OSError:
            return None
        try:
            metadata = json.loads(read_safe(session_dir / METADATA_FILENAME).text)
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(metadata, dict):
            return None
        # An empty transcript is loadable only when the session recorded no
        # messages; otherwise it is a corrupt/interrupted session that
        # load_session would reject, so keep it out of the listing.
        if msg_size == 0 and metadata.get("total_messages") != 0:
            return None
        info = _build_info(metadata, _mtime_to_utc_iso(mtime_ns))
        if info is None:
            return None
        return _Entry(mtime_ns=mtime_ns, info=info)

    def _load_persisted(self) -> dict[str, _Entry]:
        """Load the persisted index file, returning empty on corruption."""
        try:
            raw = json.loads(read_safe(self._index_path).text)
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(raw, dict):
            return {}

        entries: dict[str, _Entry] = {}
        for name, payload in raw.items():
            entry = _entry_from_payload(payload)
            if entry is None:
                return {}
            entries[name] = entry
        return entries

    def _persist(self) -> None:
        """Write the current cache to the index file, logging on failure."""
        payload = {
            name: {"mtime_ns": entry.mtime_ns, **entry.info}
            for name, entry in self._entries.items()
        }
        try:
            self._atomic_write(json.dumps(payload))
        except OSError as exc:
            logger.warning(
                "Failed to persist session index path=%s err=%s", self._index_path, exc
            )

    def _atomic_write(self, content: str) -> None:
        """Write content to a temp file and atomically replace the index file."""
        fd, tmp_name = tempfile.mkstemp(
            dir=self._save_dir, prefix=".session_index.", suffix=".tmp"
        )
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._index_path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise


_REGISTRY_LOCK = threading.Lock()
_INDEXES: dict[str, SessionIndex] = {}


def session_index_for(config: SessionLoggingConfig) -> SessionIndex:
    """Return the process-wide index for this save_dir, creating it on first use."""
    with _REGISTRY_LOCK:
        index = _INDEXES.get(config.save_dir)
        if index is None:
            # Rebuild the index
            index = SessionIndex(Path(config.save_dir), config.session_prefix)
            _INDEXES[config.save_dir] = index
        return index


def warm_session_index(config: SessionLoggingConfig) -> None:
    """Launch a background thread to warm the persisted session index"""
    index = session_index_for(config)

    def _warm() -> None:
        """Prime the index off-thread, swallowing errors."""
        try:
            index.list()
        except Exception:
            logger.exception("Failed to warm session index")

    threading.Thread(target=_warm, name="session-index-warm", daemon=True).start()


def clear_session_index_registry() -> None:
    """Reset the persisted index"""
    with _REGISTRY_LOCK:
        _INDEXES.clear()
