from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import ModelConfig, SessionLoggingConfig
from vibe.core.types import LLMChunk, LLMMessage, LLMUsage, Role


@pytest.fixture
def backend() -> FakeBackend:
    backend = FakeBackend(
        LLMChunk(
            message=LLMMessage(role=Role.assistant, content="Hi"),
            usage=LLMUsage(prompt_tokens=1, completion_tokens=1),
        )
    )
    return backend


def _create_acp_agent() -> VibeAcpAgentLoop:
    vibe_acp_agent = VibeAcpAgentLoop()
    client = FakeClient()

    vibe_acp_agent.on_connect(client)
    client.on_connect(vibe_acp_agent)

    return vibe_acp_agent  # pyright: ignore[reportReturnType]


@pytest.fixture
def acp_agent_loop(backend: FakeBackend) -> VibeAcpAgentLoop:
    class PatchedAgent(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs, backend=backend)

    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=PatchedAgent).start()
    return _create_acp_agent()


@pytest.fixture
def acp_agent_with_session_config(
    backend: FakeBackend, temp_session_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[VibeAcpAgentLoop, FakeClient]:
    session_config = SessionLoggingConfig(
        save_dir=str(temp_session_dir), session_prefix="session", enabled=True
    )
    config = build_test_vibe_config(
        active_model="devstral-latest",
        models=[
            ModelConfig(
                name="devstral-latest", provider="mistral", alias="devstral-latest"
            )
        ],
        session_logging=session_config,
    )

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **{**kwargs, "backend": backend})
            self._replace_base_config(config)
            self.agent_manager.invalidate_config()

    monkeypatch.setattr("vibe.acp.acp_agent_loop.AgentLoop", PatchedAgentLoop)
    monkeypatch.setattr(VibeAcpAgentLoop, "_load_config", lambda self: config)
    monkeypatch.setattr(
        "vibe.acp.acp_agent_loop.VibeConfig.load", lambda *args, **kwargs: config
    )

    vibe_acp_agent = VibeAcpAgentLoop()
    client = FakeClient()
    vibe_acp_agent.on_connect(client)
    client.on_connect(vibe_acp_agent)

    return vibe_acp_agent, client


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
