from __future__ import annotations

import json
from pathlib import Path

from vibe.core.utils.io import read_safe


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
            text = read_safe(self.history_file).text
        except OSError:
            self._entries = []
            return

        entries = []
        for raw_line in text.splitlines():
            raw_line = raw_line.rstrip("\n\r")
            if not raw_line:
                continue
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                entry = raw_line
            entries.append(entry if isinstance(entry, str) else str(entry))
        self._entries = entries[-self.max_entries :]

    def _save_history(self) -> None:
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with self.history_file.open("w", encoding="utf-8") as f:
                for entry in self._entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass

    def add(self, text: str) -> None:
        text = text.strip()
        if not text:
            return

        if self._entries and self._entries[-1] == text:
            return

        self._entries.append(text)

        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]

        self._save_history()
        self.reset_navigation()

    def get_previous(self, current_input: str) -> str | None:
        if not self._entries:
            return None

        if self._current_index == -1:
            self._temp_input = current_input
            self._current_index = len(self._entries)

        if self._current_index <= 0:
            return None

        self._current_index -= 1
        return self._entries[self._current_index]

    def get_next(self) -> str | None:
        if self._current_index == -1:
            return None

        if self._current_index < len(self._entries) - 1:
            self._current_index += 1
            return self._entries[self._current_index]

        result = self._temp_input
        self.reset_navigation()
        return result

    def reset_navigation(self) -> None:
        self._current_index = -1
        self._temp_input = ""
