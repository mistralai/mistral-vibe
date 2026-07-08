from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from threading import Lock

from vibe.core.logger import logger
from vibe.core.utils.io import read_safe


class HistoryManager:
    def __init__(self, history_file: Path, max_entries: int = 100) -> None:
        self.history_file = history_file
        self.max_entries = max_entries
        self._current_index: int = -1
        self._temp_input: str = ""
        self._io_lock = Lock()
        self._entries_lock = Lock()
        self._pending_entries: list[str] = []
        self._entries: list[str] = self._read_entries() or []

    def _read_entries(self) -> list[str] | None:
        if not self.history_file.exists():
            return []

        try:
            text = read_safe(self.history_file).text
        except OSError:
            return None

        entries: list[str] = []
        for raw_line in text.splitlines():
            if not raw_line:
                continue
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                entry = raw_line
            entries.append(entry if isinstance(entry, str) else str(entry))
        return entries[-self.max_entries :]

    def _write_entries(self, entries: list[str]) -> bool:
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(
                prefix=f".{self.history_file.name}.",
                suffix=".tmp",
                dir=self.history_file.parent,
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    for entry in entries:
                        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                os.replace(tmp_path, self.history_file)
            except OSError:
                Path(tmp_path).unlink(missing_ok=True)
                raise
        except OSError as exc:
            logger.warning(
                "history persist failed file=%s", self.history_file, exc_info=exc
            )
            return False
        return True

    def add(self, text: str) -> None:
        text = text.strip()
        if not text:
            return

        with self._entries_lock:
            if self._entries and self._entries[-1] == text:
                return

            self._entries.append(text)
            self._pending_entries.append(text)

            if len(self._entries) > self.max_entries:
                self._entries = self._entries[-self.max_entries :]

    def persist(self, text: str) -> None:
        # Blocking read-merge-write; run it off the UI thread. The read merges
        # entries written by other concurrent sessions sharing the file.
        text = text.strip()
        if not text:
            return

        with self._io_lock:
            with self._entries_lock:
                entries_to_persist = list(self._pending_entries)
                in_memory_entries = list(self._entries)

            if not entries_to_persist:
                return

            entries = self._read_entries()
            if entries is None:
                entries = in_memory_entries
            else:
                for entry in entries_to_persist:
                    if not entries or entries[-1] != entry:
                        entries.append(entry)
            entries = entries[-self.max_entries :]
            if not self._write_entries(entries):
                return

            with self._entries_lock:
                del self._pending_entries[: len(entries_to_persist)]
                if self._current_index == -1 and not self._pending_entries:
                    self._entries = entries

    def get_previous(self, current_input: str) -> str | None:
        with self._entries_lock:
            if not self._entries:
                return None

            if self._current_index == -1:
                self._temp_input = current_input
                self._current_index = len(self._entries)
            elif self._current_index > len(self._entries):
                self._current_index = len(self._entries)

            if self._current_index <= 0:
                return None

            self._current_index -= 1
            return self._entries[self._current_index]

    def get_next(self) -> str | None:
        with self._entries_lock:
            if self._current_index == -1:
                return None

            if self._current_index < len(self._entries) - 1:
                self._current_index += 1
                return self._entries[self._current_index]

            result = self._temp_input
            self._current_index = -1
            self._temp_input = ""
            return result

    def reset_navigation(self) -> None:
        with self._entries_lock:
            self._current_index = -1
            self._temp_input = ""

    def is_navigating(self) -> bool:
        with self._entries_lock:
            return self._current_index != -1
