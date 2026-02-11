from __future__ import annotations

from datetime import UTC, datetime, timedelta
import getpass
import json
import os
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING, Any

from anyio import NamedTemporaryFile, Path as AsyncPath

from vibe.core.types import AgentStats, LLMMessage, Role, SessionMetadata
from vibe.core.utils import is_windows, utc_now

if TYPE_CHECKING:
    from vibe.core.agents.models import AgentProfile
    from vibe.core.config import SessionLoggingConfig, VibeConfig
    from vibe.core.tools.manager import ToolManager


METADATA_FILENAME = "meta.json"
MESSAGES_FILENAME = "messages.jsonl"


class SessionLogger:
    def __init__(self, session_config: SessionLoggingConfig, session_id: str) -> None:
        self.session_config = session_config
        self.enabled = session_config.enabled

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
        folder_name = f"{self.session_prefix}_{timestamp}_{self.session_id[:8]}"
        return self.save_dir / folder_name

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

    @property
    def git_commit(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                stdin=subprocess.DEVNULL if is_windows() else None,
                text=True,
                timeout=5.0,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout.strip()
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
        return None

    @property
    def git_branch(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                stdin=subprocess.DEVNULL if is_windows() else None,
                text=True,
                timeout=5.0,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout.strip()
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
        return None

    @property
    def username(self) -> str:
        try:
            return getpass.getuser()
        except Exception:
            return "unknown"

    def _initialize_session_metadata(self) -> SessionMetadata:
        git_commit = self.git_commit
        git_branch = self.git_branch
        user_name = self.username

        return SessionMetadata(
            session_id=self.session_id,
            start_time=self.session_start_time,
            end_time=None,
            git_commit=git_commit,
            git_branch=git_branch,
            username=user_name,
            environment={"working_directory": str(Path.cwd())},
        )

    def _get_title(self, messages: list[LLMMessage]) -> str:
        first_user_message = None
        for message in messages:
            if message.role == Role.user:
                first_user_message = message
                break

        if first_user_message is None:
            title = "Untitled session"
        else:
            MAX_TITLE_LENGTH = 50
            text = str(first_user_message.content)
            title = text[:MAX_TITLE_LENGTH]
            if len(text) > MAX_TITLE_LENGTH:
                title += "…"

        return title

    @staticmethod
    async def persist_metadata(metadata: Any, session_dir: Path) -> None:
        temp_metadata_filepath = None
        metadata_filepath = session_dir / METADATA_FILENAME
        try:
            async with NamedTemporaryFile(
                mode="w",
                suffix=".json.tmp",
                dir=str(session_dir),
                delete=False,
                encoding="utf-8",
            ) as f:
                temp_metadata_filepath = Path(str(f.name))
                await f.write(json.dumps(metadata, indent=2, ensure_ascii=False))
                await f.flush()
                os.fsync(f.wrapped.fileno())

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
    async def persist_messages(messages: list[dict], session_dir: Path) -> None:
        messages_filepath = session_dir / "messages.jsonl"
        try:
            if not messages_filepath.exists():
                messages_filepath.touch()

            async with await AsyncPath(messages_filepath).open(
                "a", encoding="utf-8"
            ) as f:
                for message in messages:
                    await f.write(json.dumps(message, ensure_ascii=False) + "\n")
                    await f.flush()
                    os.fsync(f.wrapped.fileno())
        except Exception as e:
            raise RuntimeError(
                f"Failed to persist session messages to {messages_filepath}: {e}"
            ) from e

    async def save_interaction(
        self,
        messages: list[LLMMessage],
        stats: AgentStats,
        base_config: VibeConfig,
        tool_manager: ToolManager,
        agent_profile: AgentProfile,
    ) -> None:
        if not self.enabled or self.session_dir is None:
            return

        if self.session_metadata is None:
            return

        # If the session directory does not exist, create it
        try:
            self.session_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise RuntimeError(
                f"Failed to create session directory at {self.session_dir}: {type(e).__name__}: {e}"
            ) from e

        # Read old metadata and get total_messages
        try:
            if self.metadata_filepath.exists():
                async with await AsyncPath(self.metadata_filepath).open(
                    encoding="utf-8", errors="ignore"
                ) as f:
                    old_metadata = json.loads(await f.read())
                    old_total_messages = old_metadata["total_messages"]
            else:
                old_total_messages = 0
        except Exception as e:
            raise RuntimeError(
                f"Failed to read session metadata at {self.metadata_filepath}: {e}"
            ) from e

        try:
            non_system_messages = [m for m in messages if m.role != Role.system]
            # Append new messages
            new_messages = non_system_messages[old_total_messages:]

            if len(new_messages) == 0:
                return

            messages_data = [m.model_dump(exclude_none=True) for m in new_messages]
            await SessionLogger.persist_messages(messages_data, self.session_dir)

            # If message update succeeded, write metadata
            tools_available = [
                {
                    "type": "function",
                    "function": {
                        "name": tool_class.get_name(),
                        "description": tool_class.description,
                        "parameters": tool_class.get_parameters(),
                    },
                }
                for tool_class in tool_manager.available_tools.values()
            ]

            title = self._get_title(messages)
            system_prompt = (
                messages[0].model_dump()
                if len(messages) > 0 and messages[0].role == Role.system
                else None
            )
            total_messages = len(non_system_messages)

            metadata_dump = {
                **self.session_metadata.model_dump(),
                "end_time": utc_now().isoformat(),
                "stats": stats.model_dump(),
                "title": title,
                "total_messages": total_messages,
                "tools_available": tools_available,
                "config": base_config.model_dump(mode="json"),
                "agent_profile": {
                    "name": agent_profile.name,
                    "overrides": agent_profile.overrides,
                },
                "system_prompt": system_prompt,
            }

            await SessionLogger.persist_metadata(metadata_dump, self.session_dir)
        except Exception as e:
            raise RuntimeError(
                f"Failed to save session to {self.session_dir}: {e}"
            ) from e
        finally:
            self.cleanup_tmp_files()

    def reset_session(self, session_id: str) -> None:
        """Clear existing session info and setup a new session"""
        if not self.enabled:
            return

        self.session_id = session_id
        self.session_start_time = utc_now().isoformat()
        self.session_dir = self.save_folder
        self.session_metadata = self._initialize_session_metadata()

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
