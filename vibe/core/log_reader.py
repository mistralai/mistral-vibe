from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import threading

from vibe.core.logger import logger
from vibe.core.paths import LOG_FILE


@dataclass(frozen=True, slots=True)
class LogEntry:
    timestamp: datetime
    ppid: int
    pid: int
    level: str
    message: str
    raw_line: str
    line_number: int


@dataclass(frozen=True, slots=True)
class PaginatedLogs:
    entries: list[LogEntry]
    has_more: bool
    cursor: int | None


LogConsumer = Callable[[LogEntry], None]

# Format: timestamp ppid pid level message [exception]
# Timestamp is ISO 8601 from datetime.isoformat(), e.g. 2026-02-21T10:28:51.529718+00:00
DEFAULT_LOG_PATTERN = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+(?:[+-]\d{2}:\d{2})?)\s+"
    r"(\d+)\s+(\d+)\s+"
    r"(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s+"
    r"(.+)$"
)

LOG_POLL_INTERVAL = 0.5


class LogReader:
    def __init__(
        self,
        log_file: Path | None = None,
        consumer: LogConsumer | None = None,
        log_pattern: re.Pattern[str] = DEFAULT_LOG_PATTERN,
        poll_interval: float = LOG_POLL_INTERVAL,
    ) -> None:
        self._log_file = log_file if log_file is not None else LOG_FILE.path
        self._consumer = consumer
        self._log_pattern = log_pattern
        self._lock = threading.Lock()
        self._last_position: int = 0
        self._new_lines_count: int = 0
        self._stop_event: threading.Event | None = None
        self._thread: threading.Thread | None = None
        self._poll_interval = poll_interval

    @property
    def log_file(self) -> Path:
        return self._log_file

    @property
    def is_watching(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def get_logs(self, limit: int = 100, offset: int = 0) -> PaginatedLogs:
        if not self._log_file.exists():
            return PaginatedLogs(entries=[], has_more=False, cursor=None)

        entries: list[LogEntry] = []

        for line, relative_position in self._read_lines_backward(start_position=offset):
            if entry := self._parse_line(line, relative_position):
                entries.append(entry)
                if len(entries) >= limit:
                    return PaginatedLogs(
                        entries=entries, has_more=True, cursor=offset + len(entries)
                    )

        return PaginatedLogs(entries=entries, has_more=False, cursor=None)

    def _read_lines_backward(self, start_position: int) -> Iterator[tuple[str, int]]:
        if not self._log_file.exists():
            return

        with self._lock:
            # Snapshot once: file end-position is also captured once (seek EOF below),
            # so new lines appended by the poll thread after this point are invisible
            # to this iteration and the skip count stays consistent.
            adjusted_skip = start_position + self._new_lines_count
        chunk_size = 8192
        relative_position = 0
        skipped = 0

        with self._log_file.open("rb") as f:
            f.seek(0, 2)
            position = f.tell()
            remainder = b""

            while position > 0:
                read_size = min(chunk_size, position)
                position -= read_size
                f.seek(position)
                chunk = f.read(read_size) + remainder

                lines = chunk.split(b"\n")
                remainder = lines[0]

                for line in reversed(lines[1:]):
                    if not line:
                        continue
                    if skipped < adjusted_skip:
                        skipped += 1
                        continue
                    relative_position += 1
                    yield line.decode("utf-8", errors="replace"), relative_position

            if remainder:
                if skipped < adjusted_skip:
                    return
                relative_position += 1
                yield remainder.decode("utf-8", errors="replace"), relative_position

    def set_consumer(self, consumer: LogConsumer | None) -> None:
        self._consumer = consumer

    def start_watching(self) -> None:
        if self._thread and self._thread.is_alive():
            return

        with self._lock:
            self._last_position = (
                self._log_file.stat().st_size if self._log_file.exists() else 0
            )

        self._stop_event = threading.Event()
        self._thread = threading.Thread(
            target=self._poll_log_loop, name="log-reader-poll", daemon=True
        )
        self._thread.start()

    def stop_watching(self) -> None:
        if self._stop_event:
            self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1)
        self._thread = None
        self._stop_event = None
        with self._lock:
            self._new_lines_count = 0

    def shutdown(self) -> None:
        self.stop_watching()

    def _poll_log_loop(self) -> None:
        stop_event = self._stop_event
        if stop_event is None:
            return
        while not stop_event.is_set():
            stop_event.wait(self._poll_interval)
            if stop_event.is_set():
                break
            self._process_new_content()

    def _process_new_content(self) -> None:
        consumer = self._consumer
        if consumer is None:
            return

        lines: list[str]
        with self._lock:
            if not self._log_file.exists():
                self._last_position = 0
                return

            current_size = self._log_file.stat().st_size

            if current_size < self._last_position:
                self._last_position = 0
                self._new_lines_count = 0

            if current_size == self._last_position:
                return

            with self._log_file.open("r") as f:
                f.seek(self._last_position)
                new_content = f.read()
                self._last_position = f.tell()

            lines = new_content.splitlines()
            self._new_lines_count += len(lines)

        for line in lines:
            if entry := self._parse_line(line, 0):
                consumer(entry)

    def _parse_line(self, line: str, line_number: int) -> LogEntry | None:
        try:
            match = self._log_pattern.match(line)
            if not match:
                return None

            timestamp_str, ppid_str, pid_str, level, message = match.groups()

            timestamp = datetime.fromisoformat(timestamp_str)

            return LogEntry(
                timestamp=timestamp,
                ppid=int(ppid_str),
                pid=int(pid_str),
                level=level,
                message=message,
                raw_line=line,
                line_number=line_number,
            )
        except Exception:
            logger.debug("Failed to parse log line: %s", line[:100])
            return None
