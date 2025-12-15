from __future__ import annotations

from datetime import datetime
import getpass
import json
from pathlib import Path
import subprocess
from typing import TYPE_CHECKING, Any

import aiofiles

from vibe.core.llm.format import get_active_tool_classes
from vibe.core.types import AgentStats, LLMMessage, Role, SessionInfo, SessionMetadata
from vibe.core.utils import is_windows

if TYPE_CHECKING:
    from vibe.core.config import SessionLoggingConfig, VibeConfig
    from vibe.core.tools.manager import ToolManager


class InteractionLogger:
    def __init__(
        self,
        session_config: SessionLoggingConfig,
        session_id: str,
        auto_approve: bool = False,
        workdir: Path | None = None,
        session_name: str | None = None,
    ) -> None:
        if workdir is None:
            workdir = Path.cwd()
        self.session_config = session_config
        self.enabled = session_config.enabled
        self.auto_approve = auto_approve
        self.workdir = workdir
        self.session_name = session_name

        if not self.enabled:
            self.save_dir: Path | None = None
            self.session_prefix: str | None = None
            self.session_id: str = "disabled"
            self.session_start_time: str = "N/A"
            self.filepath: Path | None = None
            self.session_metadata: SessionMetadata | None = None
            return

        self.save_dir = Path(session_config.save_dir)
        self.session_prefix = session_config.session_prefix
        self.session_id = session_id
        self.session_start_time = datetime.now().isoformat()

        self.save_dir.mkdir(parents=True, exist_ok=True)
        self.filepath = self._get_save_filepath()
        self.session_metadata = self._initialize_session_metadata()

    def _get_save_filepath(self) -> Path:
        if self.save_dir is None or self.session_prefix is None:
            raise RuntimeError("Cannot get filepath when logging is disabled")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.session_prefix}_{timestamp}_{self.session_id[:8]}.json"
        return self.save_dir / filename

    def _get_git_commit(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True,
                cwd=self.workdir,
                stdin=subprocess.DEVNULL if is_windows() else None,
                text=True,
                timeout=5.0,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout.strip()
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
        return None

    def _get_git_branch(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                cwd=self.workdir,
                stdin=subprocess.DEVNULL if is_windows() else None,
                text=True,
                timeout=5.0,
            )
            if result.returncode == 0 and result.stdout:
                return result.stdout.strip()
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
            pass
        return None

    def _get_username(self) -> str:
        try:
            return getpass.getuser()
        except Exception:
            return "unknown"

    def _initialize_session_metadata(self) -> SessionMetadata:
        git_commit = self._get_git_commit()
        git_branch = self._get_git_branch()
        user_name = self._get_username()

        return SessionMetadata(
            session_id=self.session_id,
            name=self.session_name,
            start_time=self.session_start_time,
            end_time=None,
            git_commit=git_commit,
            git_branch=git_branch,
            auto_approve=self.auto_approve,
            username=user_name,
            environment={"working_directory": str(self.workdir)},
        )

    def generate_session_name(self, messages: list[LLMMessage]) -> str:
        MAX_SESSION_NAME_LENGTH = 50
        for message in messages:
            if message.role == Role.user and message.content:
                content = message.content.strip()
                return (
                    f"{content[:47]}..."
                    if len(content) > MAX_SESSION_NAME_LENGTH
                    else content
                )
        return f"Session {datetime.now().strftime('%b %d %H:%M')}"

    async def save_interaction(
        self,
        messages: list[LLMMessage],
        stats: AgentStats,
        config: VibeConfig,
        tool_manager: ToolManager,
    ) -> str | None:
        if not self.enabled or self.filepath is None:
            return None

        if self.session_metadata is None:
            return None

        if self.session_name is None and messages:
            self.session_name = self.generate_session_name(messages)
            self.session_metadata.name = self.session_name

        active_tools = get_active_tool_classes(tool_manager, config)

        tools_available = [
            {
                "type": "function",
                "function": {
                    "name": tool_class.get_name(),
                    "description": tool_class.description,
                    "parameters": tool_class.get_parameters(),
                },
            }
            for tool_class in active_tools
        ]

        interaction_data = {
            "metadata": {
                **self.session_metadata.model_dump(),
                "end_time": datetime.now().isoformat(),
                "stats": stats.model_dump(),
                "total_messages": len(messages),
                "tools_available": tools_available,
                "agent_config": config.model_dump(mode="json"),
            },
            "messages": [m.model_dump(exclude_none=True) for m in messages],
        }

        try:
            json_content = json.dumps(interaction_data, indent=2, ensure_ascii=False)

            async with aiofiles.open(self.filepath, "w", encoding="utf-8") as f:
                await f.write(json_content)

            return str(self.filepath)
        except Exception:
            return None

    def reset_session(
        self,
        session_id: str,
        filepath: Path | None = None,
        start_time: str | None = None,
    ) -> None:
        if not self.enabled:
            return

        self.session_id = session_id
        self.session_start_time = start_time or datetime.now().isoformat()
        self.session_name = None  # Reset session name for new session

        if filepath:
            self.filepath = filepath
        else:
            self.filepath = self._get_save_filepath()

        self.session_metadata = self._initialize_session_metadata()

    def update_session_name(self, name: str | None) -> None:
        if not self.enabled:
            return

        self.session_name = name
        if self.session_metadata is not None:
            self.session_metadata.name = name

    def get_session_info(
        self, messages: list[dict[str, Any]], stats: AgentStats
    ) -> SessionInfo:
        if not self.enabled or self.save_dir is None:
            return SessionInfo(
                session_id="disabled",
                start_time="N/A",
                message_count=len(messages),
                stats=stats,
                save_dir="N/A",
            )

        return SessionInfo(
            session_id=self.session_id,
            start_time=self.session_start_time,
            message_count=len(messages),
            stats=stats,
            save_dir=str(self.save_dir),
        )

    @staticmethod
    def find_latest_session(config: SessionLoggingConfig) -> Path | None:
        save_dir = Path(config.save_dir)
        if not save_dir.exists():
            return None

        pattern = f"{config.session_prefix}_*.json"
        session_files = list(save_dir.glob(pattern))

        if not session_files:
            return None

        return max(session_files, key=lambda p: p.stat().st_mtime)

    @staticmethod
    def find_session_by_id(
        session_id: str, config: SessionLoggingConfig
    ) -> Path | None:
        save_dir = Path(config.save_dir)
        if not save_dir.exists():
            return None

        # If it's a full UUID, extract the short form (first 8 chars)
        short_id = session_id.split("-")[0] if "-" in session_id else session_id

        # Try exact match first, then partial
        patterns = [
            f"{config.session_prefix}_*_{short_id}.json",  # Exact short UUID
            f"{config.session_prefix}_*_{short_id}*.json",  # Partial UUID
        ]

        for pattern in patterns:
            matches = list(save_dir.glob(pattern))
            if matches:
                return (
                    max(matches, key=lambda p: p.stat().st_mtime)
                    if len(matches) > 1
                    else matches[0]
                )

        return None

    @staticmethod
    def get_all_sessions(config: SessionLoggingConfig) -> list[dict[str, Any]]:
        save_dir = Path(config.save_dir)
        if not save_dir.exists():
            return []

        session_files = sorted(
            save_dir.glob(f"{config.session_prefix}_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        sessions = []
        for filepath in session_files:
            try:
                messages, metadata = InteractionLogger.load_session(filepath)
                sessions.append({
                    "filepath": str(filepath),
                    "metadata": metadata,
                    "message_count": len(messages),
                })
            except Exception:
                continue

        return sessions

    @staticmethod
    def rename_session_file(filepath: Path, new_name: str) -> bool:
        try:
            messages, metadata = InteractionLogger.load_session(filepath)
            metadata["name"] = new_name

            interaction_data = {
                "metadata": metadata,
                "messages": [m.model_dump(exclude_none=True) for m in messages],
            }

            json_content = json.dumps(interaction_data, indent=2, ensure_ascii=False)
            filepath.write_text(json_content, encoding="utf-8")
            return True
        except Exception:
            return False

    @staticmethod
    def load_session(filepath: Path) -> tuple[list[LLMMessage], dict[str, Any]]:
        with filepath.open("r", encoding="utf-8") as f:
            content = f.read()

        data = json.loads(content)
        messages = [LLMMessage.model_validate(msg) for msg in data.get("messages", [])]
        metadata = data.get("metadata", {})

        return messages, metadata
