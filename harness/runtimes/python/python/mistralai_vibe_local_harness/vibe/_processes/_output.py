"""Bounded raw terminal output with stable logical byte cursors."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import shutil
import threading
from typing import BinaryIO, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SEGMENT_BYTES = 8 * 1024 * 1024
RETAINED_SEGMENTS = 8
MAX_CURSOR = 9_007_199_254_740_991
MAX_PAGE_BYTES = 64_000
_SEGMENT_NAME_WIDTH = 20


class OutputUnavailableError(Exception):
    pass


class InvalidCursorError(Exception):
    def __init__(self, cursor: int, start: int, end: int) -> None:
        super().__init__("cursor is outside retained process output")
        self.cursor = cursor
        self.output_start_cursor = start
        self.bytes_available = end


class ProcessOutputStateV1(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_state_version: Literal[1] = 1
    process_id: str
    output_start_cursor: int = Field(ge=0, le=MAX_CURSOR)
    output_end_cursor: int = Field(ge=0, le=MAX_CURSOR)
    finalized: bool
    output_available: bool

    @model_validator(mode="after")
    def validate_range(self) -> Self:
        if self.output_start_cursor > self.output_end_cursor:
            raise ValueError("output start cursor exceeds its end cursor")
        if not self.output_available and (
            not self.finalized or self.output_start_cursor != self.output_end_cursor
        ):
            raise ValueError("unavailable output must be finalized with an empty range")
        return self


@dataclass(frozen=True, slots=True)
class OutputPage:
    output: bytes
    output_start_cursor: int
    next_cursor: int
    bytes_available: int
    has_more: bool
    truncated_before: bool


class ProcessOutputStore:
    """Own one process's segment files and logical cursor range."""

    def __init__(
        self,
        process_root: Path,
        state: ProcessOutputStateV1,
        *,
        segment_bytes: int = SEGMENT_BYTES,
        retained_segments: int = RETAINED_SEGMENTS,
        writer: BinaryIO | None = None,
    ) -> None:
        if segment_bytes < 1 or retained_segments < 1:
            raise ValueError("output segment limits must be positive")
        self.process_root = process_root
        self.segments_root = process_root / "segments"
        self.state_path = process_root / "output-state.json"
        self._state = state
        self._segment_bytes = segment_bytes
        self._retained_segments = retained_segments
        self._writer = writer
        self._lock = threading.Lock()

    @classmethod
    def create(
        cls,
        session_root: Path,
        process_id: str,
        *,
        segment_bytes: int = SEGMENT_BYTES,
        retained_segments: int = RETAINED_SEGMENTS,
    ) -> ProcessOutputStore:
        process_root = session_root / "processes" / process_id
        _require_safe_process_path(session_root, process_root, process_id)
        process_root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        process_root.mkdir(mode=0o700)
        segments_root = process_root / "segments"
        segments_root.mkdir(mode=0o700)
        state = ProcessOutputStateV1(
            process_id=process_id,
            output_start_cursor=0,
            output_end_cursor=0,
            finalized=False,
            output_available=True,
        )
        _replace_state(process_root / "output-state.json", state)
        writer = _open_segment(segments_root / _segment_name(0))
        return cls(
            process_root,
            state,
            segment_bytes=segment_bytes,
            retained_segments=retained_segments,
            writer=writer,
        )

    @classmethod
    def recover(
        cls,
        session_root: Path,
        process_id: str,
        *,
        segment_bytes: int = SEGMENT_BYTES,
        retained_segments: int = RETAINED_SEGMENTS,
    ) -> ProcessOutputStore:
        process_root = session_root / "processes" / process_id
        _require_safe_process_path(session_root, process_root, process_id)
        state_path = process_root / "output-state.json"
        state = _read_state(state_path, process_id)
        store = cls(
            process_root,
            state,
            segment_bytes=segment_bytes,
            retained_segments=retained_segments,
        )
        with store._lock:
            store._recover_locked()
        return store

    @classmethod
    def ensure_unavailable(
        cls, session_root: Path, process_id: str
    ) -> ProcessOutputStore:
        process_root = session_root / "processes" / process_id
        _require_safe_process_path(session_root, process_root, process_id)
        process_root.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        process_root.mkdir(mode=0o700, exist_ok=True)
        _require_real_directory(process_root)
        try:
            end = _read_state(
                process_root / "output-state.json", process_id
            ).output_end_cursor
        except (OSError, ValueError):
            end = 0
        state = ProcessOutputStateV1(
            process_id=process_id,
            output_start_cursor=end,
            output_end_cursor=end,
            finalized=True,
            output_available=False,
        )
        store = cls(process_root, state)
        with store._lock:
            store._quarantine_segments_locked()
            _replace_state(store.state_path, state)
        return store

    @classmethod
    def discard_unreferenced(cls, session_root: Path, protected_ids: set[str]) -> int:
        processes_root = session_root / "processes"
        if not processes_root.exists():
            return 0
        _require_real_directory(processes_root)
        removed = 0
        with os.scandir(processes_root) as entries:
            candidates = [
                entry.name
                for entry in entries
                if entry.name not in protected_ids
                and entry.is_dir(follow_symlinks=False)
            ]
        for process_id in candidates:
            process_root = processes_root / process_id
            try:
                _require_safe_process_path(session_root, process_root, process_id)
                _require_real_directory(process_root)
                removed += _directory_bytes(process_root)
                shutil.rmtree(process_root)
            except (OSError, ValueError):
                continue
        if candidates:
            _sync_directory(processes_root)
        return removed

    @property
    def state(self) -> ProcessOutputStateV1:
        with self._lock:
            return self._state.model_copy()

    def append(self, data: bytes) -> None:
        if not data:
            return
        with self._lock:
            if not self._state.output_available or self._state.finalized:
                raise OutputUnavailableError("process output is not writable")
            writer = self._writer
            if writer is None:
                raise OutputUnavailableError("process output writer is closed")
            view = memoryview(data)
            while view:
                opened_segment = False
                remaining_cursor_space = MAX_CURSOR - self._state.output_end_cursor
                if len(view) > remaining_cursor_space:
                    raise OutputUnavailableError("process output cursor is exhausted")
                segment_offset = self._state.output_end_cursor % self._segment_bytes
                if segment_offset == 0 and self._state.output_end_cursor != 0:
                    self._sync_writer_locked()
                    writer.close()
                    writer = _open_segment(
                        self.segments_root
                        / _segment_name(self._state.output_end_cursor)
                    )
                    self._writer = writer
                    opened_segment = True
                available = self._segment_bytes - segment_offset
                chunk = view[:available]
                written = 0
                while written < len(chunk):
                    count = writer.write(chunk[written:])
                    if not count:
                        raise OSError("process output segment write made no progress")
                    written += count
                writer.flush()
                end = self._state.output_end_cursor + len(chunk)
                self._state = self._state.model_copy(update={"output_end_cursor": end})
                view = view[len(chunk) :]
                if opened_segment or end % self._segment_bytes == 0:
                    self._sync_writer_locked()
                    self._rotate_locked()

    def read(self, *, from_end: bool, cursor: int, max_bytes: int) -> OutputPage:
        with self._lock:
            state = self._state
            if not state.output_available:
                raise OutputUnavailableError("process output is unavailable")
            limit = min(max_bytes, MAX_PAGE_BYTES)
            if limit < 1:
                raise ValueError("max_bytes must be positive")
            if from_end:
                read_start = max(
                    state.output_start_cursor, state.output_end_cursor - limit
                )
                requested = read_start
            else:
                if cursor > state.output_end_cursor:
                    raise InvalidCursorError(
                        cursor, state.output_start_cursor, state.output_end_cursor
                    )
                requested = cursor
                read_start = max(cursor, state.output_start_cursor)
            read_end = min(state.output_end_cursor, read_start + limit)
            output = self._read_range_locked(read_start, read_end)
            next_cursor = read_start + len(output)
            has_more = (
                read_start > state.output_start_cursor
                if from_end
                else next_cursor < state.output_end_cursor
            )
            truncated_before = (
                state.output_start_cursor > 0
                if from_end
                else requested < state.output_start_cursor
            )
            return OutputPage(
                output=output,
                output_start_cursor=state.output_start_cursor,
                next_cursor=state.output_end_cursor if from_end else next_cursor,
                bytes_available=state.output_end_cursor,
                has_more=has_more,
                truncated_before=truncated_before,
            )

    def finalize(self) -> ProcessOutputStateV1:
        with self._lock:
            if self._state.finalized:
                return self._state.model_copy()
            if self._writer is not None:
                self._sync_writer_locked()
                self._writer.close()
                self._writer = None
            self._remove_empty_newest_segment_locked()
            self._state = self._state.model_copy(update={"finalized": True})
            _replace_state(self.state_path, self._state)
            return self._state.model_copy()

    def mark_unavailable(self) -> ProcessOutputStateV1:
        with self._lock:
            self._mark_unavailable_locked()
            return self._state.model_copy()

    def prune(self) -> int:
        with self._lock:
            if not self._state.finalized or not self._state.output_available:
                return 0
            removed = sum(size for _, size in self._segments_locked())
            quarantine = self._quarantine_segments_locked()
            end = self._state.output_end_cursor
            try:
                self._state = self._state.model_copy(
                    update={"output_start_cursor": end, "output_end_cursor": end}
                )
                _replace_state(self.state_path, self._state)
            except BaseException:
                if quarantine is not None and not self.segments_root.exists():
                    os.replace(quarantine, self.segments_root)
                raise
            if quarantine is not None:
                shutil.rmtree(quarantine)
            return removed

    def stored_bytes(self) -> int:
        with self._lock:
            segment_bytes = (
                sum(size for _, size in self._segments_locked())
                if self.segments_root.exists()
                else 0
            )
            return segment_bytes + sum(
                _directory_bytes(path) for path in self._quarantines_locked()
            )

    def discard_quarantines(self) -> int:
        with self._lock:
            removed = 0
            for path in self._quarantines_locked():
                removed += _directory_bytes(path)
                shutil.rmtree(path)
            return removed

    def _recover_locked(self) -> None:
        if not self._state.output_available:
            self._quarantine_segments_locked()
            return
        try:
            if not self.segments_root.exists():
                if self._quarantines_locked():
                    end = self._state.output_end_cursor
                    self._state = self._state.model_copy(
                        update={"output_start_cursor": end, "finalized": True}
                    )
                    _replace_state(self.state_path, self._state)
                    return
                if (
                    self._state.finalized
                    and self._state.output_start_cursor == self._state.output_end_cursor
                ):
                    return
                raise ValueError("missing process output segments")
            segments = self._segments_locked()
            if len(segments) > 1 and segments[-1][1] == 0:
                (self.segments_root / _segment_name(segments[-1][0])).unlink()
                segments.pop()
            if not segments:
                if self._state.output_start_cursor != self._state.output_end_cursor:
                    raise ValueError("missing process output segments")
                return
            for index, (start, size) in enumerate(segments):
                if size > self._segment_bytes:
                    raise ValueError("oversized process output segment")
                if index < len(segments) - 1 and size != self._segment_bytes:
                    raise ValueError("non-final process output segment is incomplete")
                if index and start != segments[index - 1][0] + segments[index - 1][1]:
                    raise ValueError("process output segments are not contiguous")
            while len(segments) > self._retained_segments:
                start, _ = segments.pop(0)
                (self.segments_root / _segment_name(start)).unlink()
            output_start = segments[0][0]
            output_end = segments[-1][0] + segments[-1][1]
            self._state = self._state.model_copy(
                update={
                    "output_start_cursor": output_start,
                    "output_end_cursor": output_end,
                }
            )
            _replace_state(self.state_path, self._state)
        except (OSError, ValueError):
            self._mark_unavailable_locked()

    def _mark_unavailable_locked(self) -> None:
        if self._writer is not None:
            self._writer.close()
            self._writer = None
        end = self._state.output_end_cursor
        self._quarantine_segments_locked()
        self._state = self._state.model_copy(
            update={
                "output_start_cursor": end,
                "output_end_cursor": end,
                "finalized": True,
                "output_available": False,
            }
        )
        _replace_state(self.state_path, self._state)

    def _segments_locked(self) -> list[tuple[int, int]]:
        _require_real_directory(self.segments_root)
        segments: list[tuple[int, int]] = []
        with os.scandir(self.segments_root) as entries:
            for entry in entries:
                if not entry.is_file(follow_symlinks=False):
                    raise ValueError("process output contains a non-regular segment")
                start = _parse_segment_name(entry.name)
                segments.append((
                    start,
                    os.stat(entry.path, follow_symlinks=False).st_size,
                ))
        segments.sort()
        return segments

    def _read_range_locked(self, start: int, end: int) -> bytes:
        if start == end:
            return b""
        chunks: list[bytes] = []
        remaining_start = start
        remaining_end = end
        for segment_start, size in self._segments_locked():
            segment_end = segment_start + size
            if segment_end <= remaining_start or segment_start >= remaining_end:
                continue
            offset = max(remaining_start, segment_start) - segment_start
            length = min(remaining_end, segment_end) - (segment_start + offset)
            path = self.segments_root / _segment_name(segment_start)
            _require_regular_file(path)
            with path.open("rb") as segment:
                segment.seek(offset)
                chunk = segment.read(length)
            if len(chunk) != length:
                raise OSError("process output segment changed during read")
            chunks.append(chunk)
            remaining_start += len(chunk)
        if remaining_start != remaining_end:
            raise OSError("process output range is incomplete")
        return b"".join(chunks)

    def _rotate_locked(self) -> None:
        segments = self._segments_locked()
        while len(segments) > self._retained_segments:
            start, _ = segments.pop(0)
            (self.segments_root / _segment_name(start)).unlink()
        start = segments[0][0] if segments else self._state.output_end_cursor
        self._state = self._state.model_copy(update={"output_start_cursor": start})
        _sync_directory(self.segments_root)
        _replace_state(self.state_path, self._state)

    def _sync_writer_locked(self) -> None:
        if self._writer is None:
            return
        self._writer.flush()
        os.fsync(self._writer.fileno())

    def _remove_empty_newest_segment_locked(self) -> None:
        segments = self._segments_locked()
        if segments and segments[-1][1] == 0:
            (self.segments_root / _segment_name(segments[-1][0])).unlink()
            _sync_directory(self.segments_root)

    def _quarantine_segments_locked(self) -> Path | None:
        if not self.segments_root.exists():
            return None
        _require_real_directory(self.segments_root)
        quarantine = self.process_root / f".pruning-{secrets.token_hex(8)}"
        os.replace(self.segments_root, quarantine)
        _sync_directory(self.process_root)
        return quarantine

    def _quarantines_locked(self) -> list[Path]:
        quarantines: list[Path] = []
        with os.scandir(self.process_root) as entries:
            for entry in entries:
                if not entry.name.startswith(".pruning-"):
                    continue
                path = self.process_root / entry.name
                _require_real_directory(path)
                quarantines.append(path)
        return quarantines


def _read_state(path: Path, process_id: str) -> ProcessOutputStateV1:
    _require_regular_file(path)
    state = ProcessOutputStateV1.model_validate_json(path.read_bytes())
    if state.process_id != process_id:
        raise ValueError("process output state belongs to another process")
    return state


def _replace_state(path: Path, state: ProcessOutputStateV1) -> None:
    data = (
        json.dumps(
            state.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        ).encode()
        + b"\n"
    )
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as file:
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _open_segment(path: Path) -> BinaryIO:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    return os.fdopen(descriptor, "wb", buffering=0)


def _segment_name(cursor: int) -> str:
    return f"{cursor:0{_SEGMENT_NAME_WIDTH}d}.bin"


def _parse_segment_name(name: str) -> int:
    if len(name) != _SEGMENT_NAME_WIDTH + 4 or not name.endswith(".bin"):
        raise ValueError("invalid process output segment name")
    value = name[:-4]
    if not value.isascii() or not value.isdecimal():
        raise ValueError("invalid process output segment name")
    cursor = int(value)
    if cursor > MAX_CURSOR:
        raise ValueError("process output segment cursor is too large")
    return cursor


def _require_safe_process_path(
    session_root: Path, process_root: Path, process_id: str
) -> None:
    if (
        not process_id
        or process_id in {".", ".."}
        or "/" in process_id
        or "\\" in process_id
    ):
        raise ValueError("invalid process ID")
    if process_root.parent.parent != session_root:
        raise ValueError("process output path escapes its Session")
    current = session_root
    if current.exists() and _is_link(current):
        raise ValueError("Session output root cannot be a link")
    for part in process_root.relative_to(session_root).parts:
        current /= part
        if current.exists() and _is_link(current):
            raise ValueError("process output path cannot contain a link")


def _require_real_directory(path: Path) -> None:
    if _is_link(path):
        raise ValueError("process output directory cannot be a link")
    if not path.is_dir():
        raise ValueError("process output path is not a real directory")


def _require_regular_file(path: Path) -> None:
    if _is_link(path) or not path.is_file():
        raise ValueError("process output path is not a regular file")


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _directory_bytes(path: Path) -> int:
    _require_real_directory(path)
    total = 0
    for root, directories, files in os.walk(path, followlinks=False):
        root_path = Path(root)
        for name in directories:
            _require_real_directory(root_path / name)
        for name in files:
            file = root_path / name
            _require_regular_file(file)
            total += file.stat(follow_symlinks=False).st_size
    return total
