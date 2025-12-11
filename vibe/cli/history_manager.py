"""ChefChat History Manager
==========================

Manages command history with JSON-based persistence.

Improvements from original:
- Uses pathlib.Path.read_text() for safer file handling
- Skips corrupt JSON lines instead of crashing
- Added logging for debugging
- Better error recovery
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("chefchat.history")


class HistoryManager:
    """Manages command history with persistent storage."""

    def __init__(self, history_file: Path, max_entries: int = 100) -> None:
        """Initialize history manager.

        Args:
            history_file: Path to history file
            max_entries: Maximum number of entries to keep
        """
        self.history_file = history_file
        self.max_entries = max_entries
        self._entries: list[str] = []
        self._current_index: int = -1
        self._temp_input: str = ""
        self._load_history()

    def _load_history(self) -> None:
        """Load history from file with robust error handling."""
        if not self.history_file.exists():
            logger.debug("History file does not exist yet: %s", self.history_file)
            return

        try:
            # Use pathlib's read_text for safer file handling
            content = self.history_file.read_text(encoding="utf-8")
            entries = []
            skipped_lines = 0

            for line_num, raw_line in enumerate(content.splitlines(), start=1):
                raw_line = raw_line.strip()
                if not raw_line:
                    continue

                try:
                    # Try to parse as JSON
                    entry = json.loads(raw_line)
                    # Ensure it's a string
                    entry_str = entry if isinstance(entry, str) else str(entry)
                    entries.append(entry_str)
                except json.JSONDecodeError as e:
                    # Skip corrupt lines instead of crashing
                    logger.warning(
                        "Skipping corrupt JSON at line %d in %s: %s",
                        line_num,
                        self.history_file,
                        e,
                    )
                    skipped_lines += 1
                    # Treat as plain text fallback
                    if raw_line and not raw_line.startswith("{"):
                        entries.append(raw_line)

            # Keep only the most recent entries
            self._entries = entries[-self.max_entries :]

            if skipped_lines > 0:
                logger.info(
                    "Loaded %d history entries, skipped %d corrupt lines",
                    len(self._entries),
                    skipped_lines,
                )
            else:
                logger.debug("Loaded %d history entries", len(self._entries))

        except (OSError, UnicodeDecodeError) as e:
            logger.error("Failed to load history from %s: %s", self.history_file, e)
            self._entries = []

    def _save_history(self) -> None:
        """Save history to file with error handling."""
        try:
            # Ensure parent directory exists
            self.history_file.parent.mkdir(parents=True, exist_ok=True)

            # Write all entries as JSON lines
            lines = [json.dumps(entry) + "\n" for entry in self._entries]
            self.history_file.write_text("".join(lines), encoding="utf-8")

            logger.debug("Saved %d history entries to %s", len(self._entries), self.history_file)

        except OSError as e:
            logger.error("Failed to save history to %s: %s", self.history_file, e)

    def add(self, text: str) -> None:
        """Add a new entry to history.

        Args:
            text: Command text to add
        """
        text = text.strip()

        # Skip empty or command entries
        if not text or text.startswith("/"):
            return

        # Skip duplicates
        if self._entries and self._entries[-1] == text:
            logger.debug("Skipping duplicate history entry")
            return

        self._entries.append(text)

        # Trim to max entries
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries :]

        self._save_history()
        self.reset_navigation()

    def get_previous(self, current_input: str, prefix: str = "") -> str | None:
        """Get previous history entry matching prefix.

        Args:
            current_input: Current input text
            prefix: Optional prefix filter

        Returns:
            Previous matching entry or None
        """
        if not self._entries:
            return None

        # Initialize navigation
        if self._current_index == -1:
            self._temp_input = current_input
            self._current_index = len(self._entries)

        # Search backwards
        for i in range(self._current_index - 1, -1, -1):
            if self._entries[i].startswith(prefix):
                self._current_index = i
                return self._entries[i]

        return None

    def get_next(self, prefix: str = "") -> str | None:
        """Get next history entry matching prefix.

        Args:
            prefix: Optional prefix filter

        Returns:
            Next matching entry or None
        """
        if self._current_index == -1:
            return None

        # Search forwards
        for i in range(self._current_index + 1, len(self._entries)):
            if self._entries[i].startswith(prefix):
                self._current_index = i
                return self._entries[i]

        # Return to original input
        result = self._temp_input
        self.reset_navigation()
        return result

    def reset_navigation(self) -> None:
        """Reset navigation state."""
        self._current_index = -1
        self._temp_input = ""
