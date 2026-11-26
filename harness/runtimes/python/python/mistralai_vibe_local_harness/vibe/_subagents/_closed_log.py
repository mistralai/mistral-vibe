"""Retained, append-only log of the subagents a root has closed.

Closed records leave the root's working state (ADR 0039) and land here, one
canonical-JSON ``ChildSessionRecord`` per line. A record is appended and synced
before the generation that drops it is published, so a crash in between leaves
it in both places; readers drop the duplicate, the state copy winning.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path

from pydantic import ValidationError

from mistralai_vibe_local_harness.vibe._storage import _fsync_directory, canonical_json
from mistralai_vibe_local_harness.vibe._subagents._models import (
    ChildSessionRecord,
    SubagentRuntimeState,
)

CLOSED_SUBAGENTS_FILENAME = "subagents-closed.jsonl"

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Snapshot:
    signature: tuple[int, int]
    records: tuple[ChildSessionRecord, ...]
    by_name: dict[str, ChildSessionRecord]
    child_session_ids: frozenset[str]


class ClosedSubagentLog:
    def __init__(self, session_root: Path) -> None:
        self.path = session_root / CLOSED_SUBAGENTS_FILENAME
        self._snapshot: _Snapshot | None = None

    def records(self) -> tuple[ChildSessionRecord, ...]:
        return self._read().records

    def record(self, agent_name: str) -> ChildSessionRecord | None:
        record = self._read().by_name.get(agent_name)
        return None if record is None else record.model_copy(deep=True)

    def child_session_ids(self) -> frozenset[str]:
        return self._read().child_session_ids

    def append(self, records: Iterable[ChildSessionRecord]) -> None:
        current = self._read()
        added = [
            record.model_copy(deep=True)
            for record in records
            if record.child_session_id not in current.child_session_ids
        ]
        if not added:
            return
        created = not self.path.exists()
        if not created:
            _drop_torn_tail(self.path)
        with self.path.open("ab") as file:
            file.write(
                b"".join(
                    canonical_json(record.model_dump(mode="json")) + b"\n"
                    for record in added
                )
            )
            file.flush()
            os.fsync(file.fileno())
        if created:
            _fsync_directory(self.path.parent)
        stat = self.path.stat()
        self._snapshot = _snapshot(
            (stat.st_size, stat.st_mtime_ns), (*current.records, *added)
        )

    def _read(self) -> _Snapshot:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return _EMPTY
        signature = (stat.st_size, stat.st_mtime_ns)
        if self._snapshot is not None and self._snapshot.signature == signature:
            return self._snapshot
        content = self.path.read_bytes()
        lines = content.split(b"\n")
        if not content.endswith(b"\n"):
            # A crash mid-append leaves a partial last line; the next append
            # truncates it.
            lines.pop()
        records: list[ChildSessionRecord] = []
        for number, line in enumerate(lines, start=1):
            if not line:
                continue
            try:
                records.append(_closed_record(line))
            except (ValidationError, ValueError):
                # Skipping forgets one closed name; failing would stop the
                # Session from opening, closing subagents or being deleted.
                logger.warning(
                    "Skipped closed subagent log %s line %d: not a record",
                    self.path,
                    number,
                )
        self._snapshot = _snapshot(signature, tuple(records))
        return self._snapshot


def _snapshot(
    signature: tuple[int, int], records: tuple[ChildSessionRecord, ...]
) -> _Snapshot:
    return _Snapshot(
        signature=signature,
        records=records,
        by_name={record.agent_name: record for record in records},
        child_session_ids=frozenset(record.child_session_id for record in records),
    )


_EMPTY = _snapshot((0, 0), ())


def _closed_record(line: bytes) -> ChildSessionRecord:
    # A later build may add fields or states. Every record here is closed, so
    # one this build cannot fully read still reads as a closed child.
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("a closed subagent record must be an object")
    known = {
        key: item
        for key, item in value.items()
        if key in ChildSessionRecord.model_fields
    }
    try:
        return ChildSessionRecord.model_validate(known)
    except ValidationError:
        return ChildSessionRecord.model_validate({
            **known,
            "state": {"type": "tombstone"},
        })


def _drop_torn_tail(path: Path) -> None:
    keep = _complete_length(path)
    if keep is not None:
        os.truncate(path, keep)


def _complete_length(path: Path) -> int | None:
    """The length up to the last complete line, or None when nothing is torn."""
    with path.open("rb") as file:
        end = file.seek(0, os.SEEK_END)
        if end == 0:
            return None
        file.seek(end - 1)
        if file.read(1) == b"\n":
            return None
        # A crash mid-append: scan back to the last complete line.
        position = end
        while position > 0:
            step = min(4096, position)
            position -= step
            file.seek(position)
            newline = file.read(step).rfind(b"\n")
            if newline != -1:
                return position + newline + 1
    return 0


def known_child_records(
    state: SubagentRuntimeState | None, log: ClosedSubagentLog
) -> tuple[ChildSessionRecord, ...]:
    """Every child a root has spawned: its state's records, then retired ones."""
    live = tuple(state.children.values()) if state is not None else ()
    in_state = {record.child_session_id for record in live}
    return (
        *live,
        *(
            record
            for record in log.records()
            if record.child_session_id not in in_state
        ),
    )
