from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibe.core.session.session_logger import MESSAGES_FILENAME, METADATA_FILENAME
from vibe.core.types import LLMMessage

if TYPE_CHECKING:
    from vibe.core.config import SessionLoggingConfig


class SessionLoader:
    @staticmethod
    def _is_valid_session(session_dir: Path) -> bool:
        """Check if a session directory contains valid metadata and messages."""
        metadata_path = session_dir / METADATA_FILENAME
        messages_path = session_dir / MESSAGES_FILENAME

        if not metadata_path.is_file() or not messages_path.is_file():
            return False

        try:
            with open(metadata_path) as f:
                metadata = json.load(f)
                if not isinstance(metadata, dict):
                    return False

            with open(messages_path) as f:
                lines = f.readlines()
                if not lines:
                    return False
                messages = [json.loads(line) for line in lines]
                if not isinstance(messages, list) or not all(
                    isinstance(msg, dict) for msg in messages
                ):
                    return False
        except json.JSONDecodeError:
            return False

        return True

    @staticmethod
    def latest_session(session_dirs: list[Path]) -> Path | None:
        latest_dir = None
        latest_mtime = 0
        for session in session_dirs:
            if not SessionLoader._is_valid_session(session):
                continue

            messages_path = session / MESSAGES_FILENAME
            mtime = messages_path.stat().st_mtime
            if mtime > latest_mtime:
                latest_mtime = mtime
                latest_dir = session

        return latest_dir

    @staticmethod
    def find_latest_session(config: SessionLoggingConfig) -> Path | None:
        save_dir = Path(config.save_dir)
        if not save_dir.exists():
            return None

        pattern = f"{config.session_prefix}_*"
        session_dirs = list(save_dir.glob(pattern))

        return SessionLoader.latest_session(session_dirs)

    @staticmethod
    def find_session_by_id(
        session_id: str, config: SessionLoggingConfig
    ) -> Path | None:
        save_dir = Path(config.save_dir)
        if not save_dir.exists():
            return None

        short_id = session_id[:8]
        matches = list(save_dir.glob(f"{config.session_prefix}_*_{short_id}"))

        return SessionLoader.latest_session(matches)

    @staticmethod
    def load_session(filepath: Path) -> tuple[list[LLMMessage], dict[str, Any]]:
        # Load session messages from MESSAGES_FILENAME
        messages_filepath = filepath / MESSAGES_FILENAME

        try:
            with messages_filepath.open("r", encoding="utf-8") as f:
                content = f.readlines()
        except Exception as e:
            raise ValueError(
                f"Error reading session messages at {filepath}: {e}"
            ) from e

        if not len(content):
            raise ValueError(
                f"Session messages file is empty (may have been corrupted by interruption): "
                f"{filepath}"
            )

        try:
            data = [json.loads(line) for line in content]
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Session messages contain invalid JSON (may have been corrupted): "
                f"{filepath}\nDetails: {e}"
            ) from e

        messages = [
            LLMMessage.model_validate(msg) for msg in data if msg["role"] != "system"
        ]

        # Load session metadata from METADATA_FILENAME
        metadata_filepath = filepath / METADATA_FILENAME

        if metadata_filepath.exists():
            try:
                with metadata_filepath.open("r", encoding="utf-8") as f:
                    metadata = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Session metadata contains invalid JSON (may have been corrupted): "
                    f"{filepath}\nDetails: {e}"
                ) from e
        else:
            metadata = {}

        return messages, metadata
