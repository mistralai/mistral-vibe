from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from vibe.core.session.session_id import shorten_session_id
from vibe.core.types import LLMMessage, SessionMetadata
from vibe.core.utils.io import read_safe

if TYPE_CHECKING:
    from vibe.core.config import SessionLoggingConfig


METADATA_FILENAME = "meta.json"
MESSAGES_FILENAME = "messages.jsonl"


class SessionInfo(TypedDict):
    session_id: str
    cwd: str
    title: str | None
    end_time: str | None


class SessionLoader:
    @staticmethod
    def _parse_message_lines(text: str) -> list[dict[str, Any]] | None:
        lines = text.split("\n")
        if lines and lines[-1] == "":
            lines.pop()

        messages: list[dict[str, Any]] = []
        for line in lines:
            message = json.loads(line)
            if not isinstance(message, dict):
                return None
            messages.append(message)
        return messages or None

    @staticmethod
    def _same_working_directory(stored: Any, working_directory: Path) -> bool:
        if not isinstance(stored, str):
            return False
        if stored == str(working_directory):
            return True
        try:
            return Path(stored).resolve() == working_directory.resolve()
        except OSError:
            return False

    @staticmethod
    def _read_validated_session(
        session_dir: Path, working_directory: Path | None = None
    ) -> dict[str, Any] | None:
        metadata_path = session_dir / METADATA_FILENAME
        messages_path = session_dir / MESSAGES_FILENAME

        if not metadata_path.is_file() or not messages_path.is_file():
            return None

        try:
            metadata = json.loads(read_safe(metadata_path).text)
            if not isinstance(metadata, dict):
                return None
            if working_directory is not None:
                session_working_directory = (metadata.get("environment") or {}).get(
                    "working_directory"
                )
                if not SessionLoader._same_working_directory(
                    session_working_directory, working_directory
                ):
                    return None

            messages = SessionLoader._parse_message_lines(read_safe(messages_path).text)
        except (OSError, json.JSONDecodeError):
            return None

        if messages is None:
            return None

        return metadata

    @staticmethod
    def _is_valid_session(
        session_dir: Path, working_directory: Path | None = None
    ) -> bool:
        return (
            SessionLoader._read_validated_session(session_dir, working_directory)
            is not None
        )

    @staticmethod
    def latest_session(
        session_dirs: list[Path], working_directory: Path | None = None
    ) -> Path | None:
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
            if SessionLoader._is_valid_session(
                session, working_directory=working_directory
            ):
                return session

        return None

    @staticmethod
    def find_latest_session(
        config: SessionLoggingConfig, working_directory: Path | None = None
    ) -> Path | None:
        save_dir = Path(config.save_dir)
        if not save_dir.exists():
            return None

        pattern = f"{config.session_prefix}_*"
        session_dirs = list(save_dir.glob(pattern))

        return SessionLoader.latest_session(
            session_dirs, working_directory=working_directory
        )

    @staticmethod
    def find_session_by_id(
        session_id: str,
        config: SessionLoggingConfig,
        working_directory: Path | None = None,
    ) -> Path | None:
        matches = SessionLoader._find_session_dirs_by_short_id(session_id, config)

        return SessionLoader.latest_session(
            matches, working_directory=working_directory
        )

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

        short_id = shorten_session_id(session_id)
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
            metadata = SessionLoader._read_validated_session(session_dir)
            if metadata is None:
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
    def load_metadata(session_dir: Path) -> SessionMetadata:
        metadata_path = session_dir / METADATA_FILENAME
        if not metadata_path.exists():
            raise ValueError(f"Session metadata not found at {session_dir}")

        try:
            metadata_content = read_safe(metadata_path).text
            return SessionMetadata.model_validate_json(metadata_content)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(
                f"Failed to load session metadata at {session_dir}: {e}"
            ) from e

    @staticmethod
    def load_session(filepath: Path) -> tuple[list[LLMMessage], dict[str, Any]]:
        # Load session messages from MESSAGES_FILENAME
        messages_filepath = filepath / MESSAGES_FILENAME

        try:
            content = read_safe(messages_filepath).text.split("\n")
            if content and content[-1] == "":
                content.pop()
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
                metadata = json.loads(read_safe(metadata_filepath).text)
            except json.JSONDecodeError as e:
                raise ValueError(
                    f"Session metadata contains invalid JSON (may have been corrupted): "
                    f"{filepath}\nDetails: {e}"
                ) from e
        else:
            metadata = {}

        return messages, metadata

    @staticmethod
    def _clean_text(text: str) -> str:
        text = text.strip().replace("\n", " ")
        return text or "(empty message)"

    @staticmethod
    def _extract_text_from_content(content: str | None) -> str | None:
        if not content:
            return None
        return SessionLoader._clean_text(content)

    @staticmethod
    def get_first_user_message(session_id: str, config: SessionLoggingConfig) -> str:
        """Get the first user message from a session for preview."""
        session_path = SessionLoader.find_session_by_id(session_id, config)
        if not session_path:
            return "(session not found)"

        try:
            messages, _ = SessionLoader.load_session(session_path)

            for msg in messages:
                if msg.role != "user":
                    continue
                text = SessionLoader._extract_text_from_content(msg.content)
                if text:
                    return text

            return "(no user messages)"
        except ValueError:
            return "(corrupted session)"
        except OSError:
            return "(error reading session)"
