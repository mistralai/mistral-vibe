from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from vibe.core.session.session_index import (
    MESSAGES_FILENAME,
    METADATA_FILENAME,
    SessionInfo,
    session_index_for,
)
from vibe.core.types import LLMMessage, SessionMetadata
from vibe.utils.io import read_safe
from vibe.utils.session_id import shorten_session_id

if TYPE_CHECKING:
    from vibe.core.config import SessionLoggingConfig


__all__ = ["MESSAGES_FILENAME", "METADATA_FILENAME", "SessionInfo", "SessionLoader"]

# Upper bound for a first-message preview used as a fallback session label.
_PREVIEW_MAX_CHARS = 200


def _preview_snippet(text: str) -> str:
    # Cap length: the fallback label lands in the resume picker and ACP session
    # lists, which a long first-message paste would otherwise blow up. Newlines
    # are already collapsed by _clean_text.
    if len(text) > _PREVIEW_MAX_CHARS:
        return text[:_PREVIEW_MAX_CHARS].rstrip() + "…"
    return text


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
        return messages

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
    def _session_reaches(metadata: dict[str, Any], working_directory: Path) -> bool:
        """Whether this session should be offered from *working_directory*.

        The environment entry follows a move and ``origin_directory`` does not,
        so a session that has moved is findable both where it began and where
        it now sits. Sessions written before the origin was recorded carry only
        the environment entry, which is where they started and stayed.
        """
        current = (metadata.get("environment") or {}).get("working_directory")
        return any(
            SessionLoader._same_working_directory(stored, working_directory)
            for stored in (metadata.get("origin_directory"), current)
        )

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
            if working_directory is not None and not SessionLoader._session_reaches(
                metadata, working_directory
            ):
                return None

            messages = SessionLoader._parse_message_lines(read_safe(messages_path).text)
        except (OSError, json.JSONDecodeError):
            return None

        if not SessionLoader._log_is_loadable(messages, metadata):
            return None

        return metadata

    @staticmethod
    def _log_is_loadable(
        messages: list[dict[str, Any]] | None, metadata: dict[str, Any]
    ) -> bool:
        if messages is None:
            return False
        # An empty log is valid only when metadata records an empty session.
        return bool(messages) or metadata.get("total_messages") == 0

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
    def list_sessions(
        config: SessionLoggingConfig, cwd: str | None = None
    ) -> list[SessionInfo]:
        sessions = session_index_for(config).list(cwd)
        # The index yields arbitrary order; callers expect most-recent first.
        # updated_at is normalized UTC ISO, so a lexicographic sort is
        # chronological; sessions without one sort last.
        sessions.sort(key=lambda item: item["updated_at"], reverse=True)
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

        messages_filepath = filepath / MESSAGES_FILENAME
        try:
            content = read_safe(messages_filepath).text.split("\n")
            if content and content[-1] == "":
                content.pop()
        except Exception as e:
            raise ValueError(
                f"Error reading session messages at {filepath}: {e}"
            ) from e

        # An empty log is valid only when metadata records an empty session.
        if not content and metadata.get("total_messages") != 0:
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

        return messages, metadata

    @staticmethod
    def _clean_text(text: str) -> str:
        text = text.strip().replace("\n", " ")
        return text or "(empty message)"

    @staticmethod
    def _extract_text_from_content(content: Any) -> str | None:
        if isinstance(content, list):
            parts = [
                p["text"]
                for p in content
                if isinstance(p, dict) and isinstance(p.get("text"), str)
            ]
            content = "\n".join(parts)
        if not isinstance(content, str) or not content:
            return None
        return SessionLoader._clean_text(content)

    @staticmethod
    def _latest_matching_session_dir(
        session_id: str, config: SessionLoggingConfig
    ) -> Path | None:
        candidates: list[tuple[Path, float]] = []
        for session_dir in SessionLoader._find_session_dirs_by_short_id(
            session_id, config
        ):
            messages_path = session_dir / MESSAGES_FILENAME
            try:
                candidates.append((session_dir, messages_path.stat().st_mtime))
            except OSError:
                continue

        if not candidates:
            return None

        candidates.sort(key=lambda item: item[1], reverse=True)
        return candidates[0][0]

    @staticmethod
    def get_first_user_message(session_id: str, config: SessionLoggingConfig) -> str:
        """Get the first user message from a session for preview.
        Streams the transcript and stops at the first user message; never
        parses the whole conversation or runs Pydantic validation. The result is
        length-capped so an untitled session's fallback label (resume picker, ACP
        session list) can't be blown up by a long first-message paste.
        """
        session_path = SessionLoader._latest_matching_session_dir(session_id, config)
        if not session_path:
            return "(session not found)"

        try:
            content = read_safe(session_path / MESSAGES_FILENAME).text
        except OSError:
            return "(error reading session)"

        for line in content.split("\n"):
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                return "(corrupted session)"
            if not isinstance(message, dict) or message.get("role") != "user":
                continue
            text = SessionLoader._extract_text_from_content(message.get("content"))
            if text:
                return _preview_snippet(text)

        return "(no user messages)"
