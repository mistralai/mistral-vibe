from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from vibe.core.session.session_logger import MESSAGES_FILENAME, METADATA_FILENAME
from vibe.core.types import LLMMessage

if TYPE_CHECKING:
    from vibe.core.config import SessionLoggingConfig


class SessionInfo(TypedDict):
    session_id: str
    cwd: str
    title: str | None
    end_time: str | None


class SessionLoader:
    @staticmethod
    def _is_valid_session(session_dir: Path) -> bool:
        """Check if a session directory contains valid metadata and messages."""
        metadata_path = session_dir / METADATA_FILENAME
        messages_path = session_dir / MESSAGES_FILENAME

        if not metadata_path.is_file() or not messages_path.is_file():
            return False

        try:
            with metadata_path.open("r", encoding="utf-8", errors="ignore") as f:
                metadata = json.load(f)
            if not isinstance(metadata, dict):
                return False

            with messages_path.open("r", encoding="utf-8", errors="ignore") as f:
                has_messages = False
                for line in f:
                    has_messages = True
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        return False
            if not has_messages:
                return False
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return False

        return True

    @staticmethod
    def latest_session(session_dirs: list[Path]) -> Path | None:
        sessions_with_mtime: list[tuple[Path, float]] = []
        for session in session_dirs:
            messages_path = session / MESSAGES_FILENAME
            if not messages_path.is_file():
                continue
            try:
                mtime = messages_path.stat().st_mtime
                sessions_with_mtime.append((session, mtime))
            except OSError:
                continue

        if not sessions_with_mtime:
            return None

        sessions_with_mtime.sort(key=lambda x: x[1], reverse=True)

        for session, _mtime in sessions_with_mtime:
            if SessionLoader._is_valid_session(session):
                return session

        return None

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
        matches = SessionLoader._find_session_dirs_by_short_id(session_id, config)

        return SessionLoader.latest_session(matches)

    @staticmethod
    def does_session_exist(
        session_id: str, config: SessionLoggingConfig
    ) -> Path | None:
        for session_dir in SessionLoader._find_session_dirs_by_short_id(
            session_id, config
        ):
            if (session_dir / MESSAGES_FILENAME).is_file():
                return session_dir
        return None

    @staticmethod
    def _find_session_dirs_by_short_id(
        session_id: str, config: SessionLoggingConfig
    ) -> list[Path]:
        save_dir = Path(config.save_dir)
        if not save_dir.exists():
            return []

        short_id = session_id[:8]
        return list(save_dir.glob(f"{config.session_prefix}_*_{short_id}"))

    @staticmethod
    def _convert_to_utc_iso(date_str: str) -> str:
        dt = datetime.fromisoformat(date_str)
        if dt.tzinfo is None:
            dt = dt.astimezone()
        utc_dt = dt.astimezone(UTC)
        return utc_dt.isoformat()

    @staticmethod
    def list_sessions(
        config: SessionLoggingConfig, cwd: str | None = None
    ) -> list[SessionInfo]:
        save_dir = Path(config.save_dir)
        if not save_dir.exists():
            return []

        pattern = f"{config.session_prefix}_*"
        session_dirs = list(save_dir.glob(pattern))

        sessions: list[SessionInfo] = []
        for session_dir in session_dirs:
            if not SessionLoader._is_valid_session(session_dir):
                continue

            metadata_path = session_dir / METADATA_FILENAME
            try:
                with metadata_path.open("r", encoding="utf-8") as f:
                    metadata = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue

            session_id = metadata.get("session_id")
            if not session_id:
                continue

            environment = metadata.get("environment", {})
            session_cwd = environment.get("working_directory", "")

            if cwd is not None and session_cwd != cwd:
                continue

            end_time = metadata.get("end_time")
            if end_time:
                try:
                    end_time = SessionLoader._convert_to_utc_iso(end_time)
                except (ValueError, OSError):
                    end_time = None

            sessions.append({
                "session_id": session_id,
                "cwd": session_cwd,
                "title": metadata.get("title"),
                "end_time": end_time,
            })

        return sessions

    @staticmethod
    def load_session(filepath: Path) -> tuple[list[LLMMessage], dict[str, Any]]:
        # Load session messages from MESSAGES_FILENAME
        messages_filepath = filepath / MESSAGES_FILENAME

        try:
            with messages_filepath.open("r", encoding="utf-8", errors="ignore") as f:
                content = f.readlines()
        except Exception as e:
            raise ValueError(
                f"Error reading session messages at {filepath}: {e}"
            ) from e

        if not content:
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
                with metadata_filepath.open(
                    "r", encoding="utf-8", errors="ignore"
                ) as f:
                    metadata = json.load(f)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Session metadata contains invalid JSON (may have been corrupted): "
                    f"{filepath}\nDetails: {e}"
                ) from e
        else:
            metadata = {}

        return messages, metadata
