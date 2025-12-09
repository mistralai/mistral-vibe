from __future__ import annotations

import json
from pathlib import Path


class HistoryManager:
    def __init__(self, history_file: Path, max_entries: int = 100) -> None:
        self.history_file = history_file
        self.max_entries = max_entries
        self._entries: list[str] = []
        self._current_index: int = -1
        self._temp_input: str = ""
        self._load_history()

    def _load_history(self) -> None:
        if not self.history_file.exists():
            return

        try:
            with self.history_file.open("r", encoding="utf-8") as f:
                entries = []
                for raw_line in f:
                    raw_line = raw_line.rstrip("\n\r")
                    if not raw_line:
                        continue
                    try:
                        entry = json.loads(raw_line)
                    except json.JSONDecodeError:
                        entry = raw_line
                    entries.append(entry if isinstance(entry, str) else str(entry))
                self._entries = entries[-self.max_entries :]
        except (OSError, UnicodeDecodeError):
            self._entries = []

    def _save_history(self) -> None:
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with self.history_file.open("w", encoding="utf-8") as f:
                for entry in self._entries:
                    f.write(json.dumps(entry) + "\n")
        except OSError:
            pass

    def add(self, text: str) -> None:
        text = text.strip()
        if not text or text.startswith("/"):
            return

        if self._entries and self._entries[-1] == text:
            return

        self._entries.append(text)

        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]

        self._save_history()
        self.reset_navigation()

    def get_previous(self, current_input: str, prefix: str = "") -> str | None:
        if not self._entries:
            return None

        if self._current_index == -1:
            self._temp_input = current_input
            self._current_index = len(self._entries)

        for i in range(self._current_index - 1, -1, -1):
            if self._entries[i].startswith(prefix):
                self._current_index = i
                return self._entries[i]

        return None

    def get_next(self, prefix: str = "") -> str | None:
        if self._current_index == -1:
            return None

        for i in range(self._current_index + 1, len(self._entries)):
            if self._entries[i].startswith(prefix):
                self._current_index = i
                return self._entries[i]

        result = self._temp_input
        self.reset_navigation()
        return result

    def reset_navigation(self) -> None:
        self._current_index = -1
        self._temp_input = ""
