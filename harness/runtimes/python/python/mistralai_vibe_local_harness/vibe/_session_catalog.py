"""Rebuildable metadata catalogue for persisted Unified sessions.

`UnifiedSessionCatalog.entries()` reconciles one small cache document against
every session's `CURRENT` pointer and active recovery journal. Full private
stores remain authoritative and are loaded only for new or changed entries.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
import hashlib
import logging
import os
from pathlib import Path
import stat
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mistralai_vibe_local_harness.session_protocol import PublicSession
from mistralai_vibe_local_harness.vibe._projection import with_session_preview
from mistralai_vibe_local_harness.vibe._storage import (
    STORE_FORMAT_MINOR,
    CurrentPointerV1,
    ImportProvenanceV1,
    SessionMetadataV1,
    UnifiedSessionStore,
    _read_document_bytes,
    _reject_symlink_components,
    _replace_document,
)
from mistralai_vibe_local_harness.vibe._subagents._models import SessionIdentity

_CATALOG_FILENAME = ".session-index.json"

logger = logging.getLogger(__name__)

# Writers replace a document by renaming a new file over it and never rewrite
# one in place, so an unchanged inode, size, and timestamps mean unchanged bytes.
type _FileSignature = tuple[int, int, int, int]


def _file_signature(result: os.stat_result) -> _FileSignature:
    return (result.st_ino, result.st_size, result.st_mtime_ns, result.st_ctime_ns)


@dataclass(frozen=True, slots=True)
class _PointerDigest:
    signature: _FileSignature
    current_sha256: str
    journal_path: str


@dataclass(slots=True)
class SessionCatalogCache:
    """Parsed catalogue state one process reuses across reconciliations.

    Nothing here is persisted. Each read still compares file signatures, so a
    change written by any process is picked up on the next reconciliation.
    """

    document: tuple[_FileSignature, _SessionCatalogV1] | None = None
    pointers: dict[str, _PointerDigest] = field(default_factory=dict)


class _CatalogModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionCatalogKeyV1(_CatalogModel):
    current_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    journal_path: str = Field(pattern=r"^journal/[0-9]{16}\.jsonl$")
    journal_size: int = Field(ge=0)
    # Nanosecond timestamps exceed RFC 8785's interoperable integer range, so
    # preserve the exact filesystem value as decimal text.
    journal_mtime_ns: str = Field(pattern=r"^[0-9]+$")


class SessionCatalogEntryV1(_CatalogModel):
    session_id: str
    key: SessionCatalogKeyV1
    session: PublicSession
    metadata: SessionMetadataV1
    identity: SessionIdentity
    import_provenance: ImportProvenanceV1 | None = None

    @model_validator(mode="after")
    def validate_session_identity(self) -> Self:
        if (
            self.session.id != self.session_id
            or self.identity.session_id != self.session_id
            or self.metadata.root_session_id != self.identity.root_session_id
            or self.metadata.parent_session_id != self.identity.parent_session_id
        ):
            raise ValueError("catalogue entry session identities disagree")
        return self


class _SessionCatalogV1(_CatalogModel):
    catalog_version: Literal[1] = 1
    entries: list[SessionCatalogEntryV1]

    @model_validator(mode="after")
    def validate_entry_order(self) -> Self:
        session_ids = [entry.session_id for entry in self.entries]
        if session_ids != sorted(set(session_ids)):
            raise ValueError("catalogue entries must have unique sorted session IDs")
        return self


class UnifiedSessionCatalog:
    """Read and reconcile the persisted query projection of Unified stores."""

    def __init__(
        self, storage_root: Path, cache: SessionCatalogCache | None = None
    ) -> None:
        self._storage_root = storage_root
        self._unified_root = storage_root / "unified"
        self._path = self._unified_root / _CATALOG_FILENAME
        self._cache = cache if cache is not None else SessionCatalogCache()

    def entries(self) -> tuple[SessionCatalogEntryV1, ...]:
        if not self._unified_root.exists():
            return ()
        cached = self._read()
        cached_by_id = (
            {entry.session_id: entry for entry in cached.entries}
            if cached is not None
            else {}
        )
        reconciled: list[SessionCatalogEntryV1] = []
        seen: set[str] = set()
        for session_root in sorted(
            self._unified_root.iterdir(), key=lambda path: path.name
        ):
            if not session_root.is_dir() or session_root.is_symlink():
                continue
            session_id = session_root.name
            seen.add(session_id)
            try:
                key = self._read_key(session_id)
                prior = cached_by_id.get(session_id)
                if prior is not None and prior.key == key:
                    reconciled.append(prior)
                    continue
                entry = self._load_entry(session_id, key)
            except Exception:
                logger.warning(
                    "Skipping unloadable session %s", session_id, exc_info=True
                )
                continue
            reconciled.append(entry)

        # Concurrent sweeps share the cache and insert while this one prunes, so
        # iterate this sweep's own set and only look the shared mapping up.
        pointers = self._cache.pointers
        self._cache.pointers = {
            session_id: digest
            for session_id in seen
            if (digest := pointers.get(session_id)) is not None
        }
        document = _SessionCatalogV1(entries=reconciled)
        self._write_if_changed(cached, document)
        return tuple(document.entries)

    def entry(
        self, session_id: str, store: UnifiedSessionStore | None = None
    ) -> SessionCatalogEntryV1 | None:
        """Read one entry without reconciling every unrelated session."""
        found = self._entries_for((session_id,), store)
        return found[0] if found else None

    def entries_for(
        self, session_ids: Iterable[str]
    ) -> tuple[SessionCatalogEntryV1, ...]:
        """Read these entries, in this order, without reconciling every
        unrelated session; an id with no loadable store is left out.
        """
        return self._entries_for(dict.fromkeys(session_ids), None)

    def _entries_for(
        self, session_ids: Iterable[str], store: UnifiedSessionStore | None
    ) -> tuple[SessionCatalogEntryV1, ...]:
        if not self._unified_root.exists():
            return ()
        cached = self._read()
        by_id = (
            {entry.session_id: entry for entry in cached.entries}
            if cached is not None
            else {}
        )
        found: list[SessionCatalogEntryV1] = []
        for session_id in session_ids:
            prior = by_id.get(session_id)
            try:
                key = self._read_key(session_id)
                if prior is not None and prior.key == key:
                    found.append(prior)
                    continue
                entry = self._load_entry(session_id, key, store)
            except Exception:
                continue
            by_id[session_id] = entry
            found.append(entry)

        document = _SessionCatalogV1(
            entries=sorted(by_id.values(), key=lambda item: item.session_id)
        )
        self._write_if_changed(cached, document)
        return tuple(found)

    def _read(self) -> _SessionCatalogV1 | None:
        try:
            signature = _file_signature(os.lstat(self._path))
            if self._cache.document is not None:
                cached_signature, cached = self._cache.document
                if cached_signature == signature:
                    return cached
            value, _canonical = _read_document_bytes(self._path)
            document = _SessionCatalogV1.model_validate(value)
        except Exception:
            return None
        self._cache.document = (signature, document)
        return document

    def _write_if_changed(
        self, cached: _SessionCatalogV1 | None, document: _SessionCatalogV1
    ) -> None:
        if cached == document:
            return
        try:
            _replace_document(
                self._path, document.model_dump(mode="json", by_alias=True)
            )
            # Another writer may replace the file between these two calls. The
            # remembered document is then stale, which only costs reloads: every
            # entry is still checked against its session's current key.
            self._cache.document = (_file_signature(os.lstat(self._path)), document)
        except OSError:
            # The catalogue is only an optimization. Read-only stores and
            # concurrent cache writers must not make session discovery fail.
            return

    def _load_entry(
        self,
        session_id: str,
        key: SessionCatalogKeyV1,
        store: UnifiedSessionStore | None = None,
    ) -> SessionCatalogEntryV1:
        store = store or UnifiedSessionStore(self._storage_root, session_id)
        stored = store.load()
        # Cache the raw preview; liveness is dynamic, so ``list`` settles a
        # mid-turn row per query against the lease rather than baking it in here.
        snapshot = with_session_preview(stored.projection_state.snapshot)
        # Keep the key sampled before the load. If the store advances during
        # this read, the next reconciliation sees a mismatch and refreshes the
        # entry; retrying here could spin for the lifetime of an active turn.
        return SessionCatalogEntryV1(
            session_id=session_id,
            key=key,
            session=snapshot.session,
            metadata=stored.runtime_state.session_metadata,
            identity=stored.runtime_state.identity,
            import_provenance=stored.runtime_state.import_provenance,
        )

    def _read_key(self, session_id: str) -> SessionCatalogKeyV1:
        store = UnifiedSessionStore(self._storage_root, session_id)
        _reject_symlink_components(self._storage_root, store.session_root)
        pointer = self._read_pointer(session_id, store.session_root / "CURRENT")
        journal_path = store.session_root / pointer.journal_path
        _reject_symlink_components(store.session_root, journal_path)
        journal_stat = os.lstat(journal_path)
        if not stat.S_ISREG(journal_stat.st_mode):
            raise ValueError("recovery journal must be a regular file")
        return SessionCatalogKeyV1(
            current_sha256=pointer.current_sha256,
            journal_path=pointer.journal_path,
            journal_size=journal_stat.st_size,
            journal_mtime_ns=str(journal_stat.st_mtime_ns),
        )

    def _read_pointer(self, session_id: str, current_path: Path) -> _PointerDigest:
        # Sampled before the read: a replacement in between leaves the new bytes
        # filed under the old signature, which never matches again.
        signature = _file_signature(os.lstat(current_path))
        cached = self._cache.pointers.get(session_id)
        if cached is not None and cached.signature == signature:
            return cached
        current_value, current_bytes = _read_document_bytes(current_path)
        current = CurrentPointerV1.model_validate(current_value)
        if current.session_id != session_id:
            raise ValueError("CURRENT names another session")
        if current.store_format_minor > STORE_FORMAT_MINOR:
            raise ValueError("CURRENT requires a newer store reader")
        digest = _PointerDigest(
            signature=signature,
            current_sha256=hashlib.sha256(current_bytes).hexdigest(),
            journal_path=f"journal/{current.snapshot_sequence + 1:016d}.jsonl",
        )
        self._cache.pointers[session_id] = digest
        return digest


__all__ = [
    "SessionCatalogCache",
    "SessionCatalogEntryV1",
    "SessionCatalogKeyV1",
    "UnifiedSessionCatalog",
]
