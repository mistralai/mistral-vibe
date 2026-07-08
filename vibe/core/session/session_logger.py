from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
from threading import Lock
from typing import TYPE_CHECKING, Any, Literal

from vibe.core.session.session_id import shorten_session_id
from vibe.core.session.session_loader import (
    MESSAGES_FILENAME,
    METADATA_FILENAME,
    SessionLoader,
)
from vibe.core.session.title_format import MAX_TITLE_LENGTH
from vibe.core.types import AgentStats, LLMMessage, Role, SessionMetadata
from vibe.core.utils import is_windows, utc_now
from vibe.core.utils.io import read_safe, read_safe_async

if TYPE_CHECKING:
    from vibe.core.agents.models import AgentProfile
    from vibe.core.config import AnyVibeConfig, SessionLoggingConfig
    from vibe.core.experiments.models import EvalResponse
    from vibe.core.tools.manager import ToolManager


TMP_CLEANUP_INTERVAL = timedelta(seconds=5)


class SessionLogger:
    def __init__(self, session_config: SessionLoggingConfig, session_id: str) -> None:
        self.session_config = session_config
        self.enabled = session_config.enabled
        self._last_tmp_cleanup_at: datetime | None = None
        self._tmp_cleanup_lock = Lock()
        # Serializes writes so concurrent saves cannot interleave appends to
        # messages.jsonl or race on the metadata read-modify-write.
        self._save_lock = asyncio.Lock()

        if not self.enabled:
            self.save_dir: Path | None = None
            self.session_prefix: str | None = None
            self.session_id: str = "disabled"
            self.session_start_time: str = "N/A"
            self.session_dir: Path | None = None
            self.session_metadata: SessionMetadata | None = None
            return

        self.save_dir = Path(session_config.save_dir)
        self.session_prefix = session_config.session_prefix
        self.session_id = session_id
        self.session_start_time = utc_now().isoformat()

        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.session_dir = self.save_folder
        self.session_metadata = self._initialize_session_metadata()

    @property
    def save_folder(self) -> Path:
        if self.save_dir is None or self.session_prefix is None:
            raise RuntimeError(
                "Cannot get session save folder when logging is disabled"
            )

        timestamp = utc_now().strftime("%Y%m%d_%H%M%S")
        folder_name = (
            f"{self.session_prefix}_{timestamp}_{shorten_session_id(self.session_id)}"
        )
        return self.save_dir / folder_name

    def _get_session_info(self) -> tuple[Path, SessionMetadata] | None:
        if (
            not self.enabled
            or self.session_dir is None
            or self.session_metadata is None
        ):
            return None
        return (self.session_dir, self.session_metadata)

    @property
    def metadata_filepath(self) -> Path:
        if self.session_dir is None:
            raise RuntimeError(
                "Cannot get session metadata filepath when logging is disabled"
            )
        return self.session_dir / METADATA_FILENAME

    @property
    def messages_filepath(self) -> Path:
        if self.session_dir is None:
            raise RuntimeError(
                "Cannot get session messages filepath when logging is disabled"
            )
        return self.session_dir / MESSAGES_FILENAME

    def _fetch_git_metadata(self) -> tuple[str | None, str | None]:
        """Fetch git commit and branch in a single subprocess call."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD", "--abbrev-ref", "HEAD"],
                capture_output=True,
                stdin=subprocess.DEVNULL if is_windows() else None,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=5.0,
            )
            if result.returncode == 0 and result.stdout:
                lines = result.stdout.strip().splitlines()
                commit = lines[0] if len(lines) > 0 else None
                branch = lines[1] if len(lines) > 1 else None
                return commit, branch
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
        return None, None

    @property
    def git_commit(self) -> str | None:
        return self._fetch_git_metadata()[0]

    @property
    def git_branch(self) -> str | None:
        return self._fetch_git_metadata()[1]

    @property
    def username(self) -> str:
        try:
            return getpass.getuser()
        except Exception:
            return "unknown"

    def _initialize_session_metadata(self) -> SessionMetadata:
        git_commit, git_branch = self._fetch_git_metadata()
        user_name = self.username

        return SessionMetadata(
            session_id=self.session_id,
            start_time=self.session_start_time,
            end_time=None,
            git_commit=git_commit,
            git_branch=git_branch,
            username=user_name,
            environment={"working_directory": str(Path.cwd())},
            title=None,
            title_source="auto",
        )

    def _fallback_title_from_messages(self, messages: Sequence[LLMMessage]) -> str:
        first_user_message = None
        for message in messages:
            if message.role == Role.user:
                first_user_message = message
                break

        if first_user_message is None:
            return "Untitled session"

        text = str(first_user_message.content)
        title = text[:MAX_TITLE_LENGTH]
        if len(text) > MAX_TITLE_LENGTH:
            title += "…"
        return title

    def _set_title_state(
        self, title: str | None, *, source: Literal["auto", "manual"]
    ) -> None:
        if self.session_metadata is None:
            return

        self.session_metadata.title = title
        self.session_metadata.title_source = source

    def set_title(self, title: str | None) -> None:
        if title is None:
            self._set_title_state(None, source="auto")
            return

        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("Session title cannot be empty.")

        self._set_title_state(normalized_title, source="manual")

    def needs_initial_auto_title(self) -> bool:
        return self.session_metadata is not None and self.session_metadata.title is None

    def set_initial_auto_title(self, title: str) -> bool:
        if not self.needs_initial_auto_title():
            return False

        normalized_title = title.strip()
        if not normalized_title:
            return False

        self._set_title_state(normalized_title, source="auto")
        return True

    def _resolve_title(self, messages: Sequence[LLMMessage]) -> str | None:
        if self.session_metadata is None:
            return self._fallback_title_from_messages(messages)

        if self.session_metadata.title is not None:
            return self.session_metadata.title

        title = self._fallback_title_from_messages(messages)
        self._set_title_state(title, source="auto")
        return title

    @staticmethod
    def _persist_metadata_sync(metadata: Any, session_dir: Path) -> None:
        temp_metadata_filepath = None
        metadata_filepath = session_dir / METADATA_FILENAME
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".json.tmp",
                dir=str(session_dir),
                delete=False,
                encoding="utf-8",
            ) as f:
                temp_metadata_filepath = Path(f.name)
                f.write(json.dumps(metadata, indent=2, ensure_ascii=False))
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_metadata_filepath, str(metadata_filepath))
        except Exception as e:
            raise RuntimeError(
                f"Failed to persist session metadata to {metadata_filepath}: {e}"
            ) from e
        finally:
            if (
                temp_metadata_filepath
                and temp_metadata_filepath.exists()
                and temp_metadata_filepath.is_file()
            ):
                temp_metadata_filepath.unlink()

    @staticmethod
    async def persist_metadata(metadata: Any, session_dir: Path) -> None:
        await asyncio.to_thread(
            SessionLogger._persist_metadata_sync, metadata, session_dir
        )

    @staticmethod
    def _persist_messages_sync(messages: list[dict], session_dir: Path) -> None:
        messages_filepath = session_dir / "messages.jsonl"
        try:
            with messages_filepath.open("a", encoding="utf-8") as f:
                for message in messages:
                    f.write(json.dumps(message, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except Exception as e:
            raise RuntimeError(
                f"Failed to persist session messages to {messages_filepath}: {e}"
            ) from e

    @staticmethod
    async def persist_messages(messages: list[dict], session_dir: Path) -> None:
        await asyncio.to_thread(
            SessionLogger._persist_messages_sync, messages, session_dir
        )

    @staticmethod
    def _overwrite_messages_sync(messages: list[dict], session_dir: Path) -> None:
        messages_filepath = session_dir / MESSAGES_FILENAME
        temp_filepath = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".jsonl.tmp",
                dir=str(session_dir),
                delete=False,
                encoding="utf-8",
            ) as f:
                temp_filepath = Path(f.name)
                for message in messages:
                    f.write(json.dumps(message, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())

            os.replace(temp_filepath, str(messages_filepath))
        except Exception as e:
            raise RuntimeError(
                f"Failed to overwrite session messages at {messages_filepath}: {e}"
            ) from e
        finally:
            if temp_filepath and temp_filepath.exists() and temp_filepath.is_file():
                temp_filepath.unlink()

    @staticmethod
    def _message_fingerprint(message: LLMMessage) -> str:
        payload = json.dumps(
            message.model_dump(exclude_none=True, mode="json"), sort_keys=True
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def save_interaction(
        self,
        messages: Sequence[LLMMessage],
        stats: AgentStats,
        base_config: AnyVibeConfig,
        tool_manager: ToolManager,
        agent_profile: AgentProfile,
        *,
        allow_empty: bool = False,
    ) -> None:
        session_info = self._get_session_info()
        if session_info is None:
            return
        session_dir, session_metadata = session_info

        non_system_messages = [m for m in messages if m.role != Role.system]

        # Empty conversations are only persisted on explicit opt-in (rewind to
        # the first message); otherwise an empty log would be unloadable.
        if not non_system_messages and not allow_empty:
            return

        # Snapshot the message list and resolve the title on the event loop,
        # then hand everything to a worker thread: serialization and fsync are
        # too slow to run on the UI thread after every agent turn.
        messages_snapshot = list(messages)
        title = self._resolve_title(messages_snapshot)
        async with self._save_lock:
            await asyncio.to_thread(
                self._save_interaction_sync,
                messages_snapshot,
                stats,
                base_config,
                tool_manager,
                agent_profile,
                title,
                session_dir,
                session_metadata,
                allow_empty,
            )

    def _save_interaction_sync(
        self,
        messages: list[LLMMessage],
        stats: AgentStats,
        base_config: AnyVibeConfig,
        tool_manager: ToolManager,
        agent_profile: AgentProfile,
        title: str | None,
        session_dir: Path,
        session_metadata: SessionMetadata,
        allow_empty: bool,
    ) -> None:
        metadata_path = session_dir / METADATA_FILENAME

        # If the session directory does not exist, create it
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(
                f"Failed to create session directory at {session_dir}: {type(e).__name__}: {e}"
            ) from e

        # Read old metadata to detect appends, rewinds, and edited tails.
        try:
            if metadata_path.exists():
                old_metadata = json.loads(read_safe(metadata_path).text)
                old_total_messages = old_metadata["total_messages"]
                old_last_fingerprint = old_metadata.get("last_message_fingerprint")
            else:
                old_total_messages = 0
                old_last_fingerprint = None
        except Exception as e:
            raise RuntimeError(
                f"Failed to read session metadata at {metadata_path}: {e}"
            ) from e

        non_system_messages = [m for m in messages if m.role != Role.system]

        if not non_system_messages and not allow_empty:
            return

        # A missing fingerprint (legacy session) can't verify the boundary, so
        # it forces a full rewrite rather than a no-op or append.
        boundary_unchanged = old_total_messages == 0 or (
            old_last_fingerprint is not None
            and old_total_messages <= len(non_system_messages)
            and self._message_fingerprint(non_system_messages[old_total_messages - 1])
            == old_last_fingerprint
        )
        if len(non_system_messages) == old_total_messages and boundary_unchanged:
            return

        try:
            if len(non_system_messages) > old_total_messages and boundary_unchanged:
                messages_data = [
                    m.model_dump(exclude_none=True, mode="json")
                    for m in non_system_messages[old_total_messages:]
                ]
                SessionLogger._persist_messages_sync(messages_data, session_dir)
            else:
                messages_data = [
                    m.model_dump(exclude_none=True, mode="json")
                    for m in non_system_messages
                ]
                SessionLogger._overwrite_messages_sync(messages_data, session_dir)

            # If message update succeeded, write metadata
            tools_available = [
                {"type": "function", "function": fn.model_dump()}
                for fn in tool_manager.available_tool_specs()
            ]

            system_prompt = (
                messages[0].model_dump()
                if len(messages) > 0 and messages[0].role == Role.system
                else None
            )
            last_message_fingerprint = (
                self._message_fingerprint(non_system_messages[-1])
                if non_system_messages
                else None
            )

            metadata_dump = {
                **session_metadata.model_dump(),
                "end_time": utc_now().isoformat(),
                "stats": stats.model_dump(),
                "title": title,
                "total_messages": len(non_system_messages),
                "last_message_fingerprint": last_message_fingerprint,
                "tools_available": tools_available,
                "config": base_config.model_dump(mode="json"),
                "agent_profile": {
                    "name": agent_profile.name,
                    "overrides": agent_profile.overrides,
                },
                "system_prompt": system_prompt,
            }

            SessionLogger._persist_metadata_sync(metadata_dump, session_dir)
        except Exception as e:
            raise RuntimeError(f"Failed to save session to {session_dir}: {e}") from e
        finally:
            self.maybe_cleanup_tmp_files()

    async def persist_loops(self) -> None:
        session_info = self._get_session_info()
        if session_info is None:
            return
        session_dir, session_metadata = session_info
        metadata_path = session_dir / METADATA_FILENAME
        if not metadata_path.exists():
            return
        async with self._save_lock:
            try:
                raw = (await read_safe_async(metadata_path)).text
                metadata = json.loads(raw)
            except (OSError, json.JSONDecodeError) as e:
                raise RuntimeError(
                    f"Failed to read session metadata at {metadata_path}: {e}"
                ) from e
            metadata["loops"] = [
                loop.model_dump(mode="json") for loop in session_metadata.loops
            ]
            await SessionLogger.persist_metadata(metadata, session_dir)

    async def persist_experiments(self, response: EvalResponse | None) -> None:
        session_info = self._get_session_info()
        if session_info is None:
            return
        session_dir, session_metadata = session_info
        session_metadata.experiments = response
        metadata_path = session_dir / METADATA_FILENAME
        if not metadata_path.exists():
            return
        async with self._save_lock:
            try:
                raw = (await read_safe_async(metadata_path)).text
                metadata = json.loads(raw)
            except (OSError, json.JSONDecodeError) as e:
                raise RuntimeError(
                    f"Failed to read session metadata at {metadata_path}: {e}"
                ) from e
            metadata["experiments"] = (
                response.model_dump(mode="json") if response is not None else None
            )
            await SessionLogger.persist_metadata(metadata, session_dir)

    def reset_session(
        self, session_id: str, *, parent_session_id: str | None = None
    ) -> None:
        """Clear existing session info and setup a new session."""
        if not self.enabled:
            return

        self.session_id = session_id
        self.session_start_time = utc_now().isoformat()
        self.session_dir = self.save_folder
        self.session_metadata = self._initialize_session_metadata()
        if parent_session_id is not None:
            self.session_metadata.parent_session_id = parent_session_id

    def resume_existing_session(self, session_id: str, session_dir: Path) -> None:
        if not self.enabled:
            return

        self.session_id = session_id
        self.session_dir = session_dir
        self.session_metadata = SessionLoader.load_metadata(session_dir)

        if self.session_metadata.start_time:
            self.session_start_time = self.session_metadata.start_time

    def cleanup_tmp_files(self) -> None:
        """Delete temporary files created more than 5 minutes ago"""
        if not self.enabled or not self.save_dir:
            return

        now = utc_now()
        ago = now - timedelta(minutes=5)

        tmp_files = self.save_dir.glob("**/*.json.tmp")  # Recursive search

        for file_path in tmp_files:
            if file_path.is_file():
                try:
                    file_mtime = datetime.fromtimestamp(
                        file_path.stat().st_mtime, tz=UTC
                    )
                    if file_mtime < ago:
                        file_path.unlink()
                except Exception:
                    continue

    def maybe_cleanup_tmp_files(self) -> None:
        if not self.enabled or not self.save_dir:
            return

        if not self._tmp_cleanup_lock.acquire(blocking=False):
            return
        try:
            now = utc_now()
            if (
                self._last_tmp_cleanup_at is not None
                and now - self._last_tmp_cleanup_at < TMP_CLEANUP_INTERVAL
            ):
                return

            self.cleanup_tmp_files()
            self._last_tmp_cleanup_at = now
        finally:
            self._tmp_cleanup_lock.release()
