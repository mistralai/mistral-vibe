from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest

from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import SessionStarter, VibeAcpAgent


def _create_acp_agent(session_starter: SessionStarter | None = None) -> VibeAcpAgent:
    agent = (
        VibeAcpAgent(session_starter=session_starter)
        if session_starter
        else VibeAcpAgent()
    )
    client = FakeClient()
    agent.on_connect(client)
    client.on_connect(agent)
    return agent


@pytest.fixture
def temp_session_dir(tmp_path: Path) -> Path:
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    return session_dir


@pytest.fixture
def create_test_session():
    """Create a test session with configurable messages and metadata.

    Supports both messages parameter (for load_session tests) and
    end_time parameter (for list_sessions tests).
    """

    def _create_session(
        session_dir: Path,
        session_id: str,
        cwd: str,
        messages: list[dict] | None = None,
        title: str | None = None,
        end_time: str | None = None,
        parent_session_id: str | None = None,
    ) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_folder = session_dir / f"session_{timestamp}_{session_id[:8]}"
        session_folder.mkdir(exist_ok=True)

        if messages is None:
            messages = [{"role": "user", "content": "Hello"}]

        messages_file = session_folder / "messages.jsonl"
        with messages_file.open("w", encoding="utf-8") as f:
            for msg in messages:
                f.write(json.dumps(msg) + "\n")

        metadata = {
            "session_id": session_id,
            "start_time": "2024-01-01T12:00:00Z",
            "end_time": end_time or "2024-01-01T12:05:00Z",
            "git_commit": None,
            "git_branch": None,
            "username": "test-user",
            "environment": {"working_directory": cwd},
            "title": title,
        }
        if parent_session_id is not None:
            metadata["parent_session_id"] = parent_session_id

        metadata_file = session_folder / "meta.json"
        with metadata_file.open("w", encoding="utf-8") as f:
            json.dump(metadata, f)

        return session_folder

    return _create_session
