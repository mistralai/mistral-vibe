from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.stubs.app_server import start_test_app_server
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.agent import SessionStarter, VibeAcpAgent
from vibe.app_server.local import LocalHarnessOptions, ResumeSessionIntent
from vibe.app_server.session import AppServerSession
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


def _create_acp_agent(session_starter: SessionStarter | None = None) -> VibeAcpAgent:
    vibe_acp_agent = (
        VibeAcpAgent(session_starter=session_starter)
        if session_starter is not None
        else VibeAcpAgent()
    )
    client = FakeClient()

    vibe_acp_agent.on_connect(client)
    client.on_connect(vibe_acp_agent)

    return vibe_acp_agent  # pyright: ignore[reportReturnType]


@pytest.fixture
def acp_agent_loop(backend: FakeBackend) -> VibeAcpAgent:
    async def start_session(options: LocalHarnessOptions) -> AppServerSession:
        loop = build_test_agent_loop(backend=backend, enable_streaming=True)
        return await AppServerSession.start(
            start_test_app_server(loop),
            client_info=options.client.info,
            capabilities=options.client.capabilities,
            session_options=options.session_options,
        )

    return _create_acp_agent(start_session)


@pytest.fixture
def acp_agent_with_session_config(
    backend: FakeBackend, temp_session_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[VibeAcpAgent, FakeClient]:
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

    async def start_session(options: LocalHarnessOptions) -> AppServerSession:
        loop = build_test_agent_loop(
            config=config, backend=backend, enable_streaming=True
        )
        return await AppServerSession.start(
            start_test_app_server(loop),
            client_info=options.client.info,
            capabilities=options.client.capabilities,
            session_options=options.session_options,
            resume_session_id=(
                options.session.session_id
                if isinstance(options.session, ResumeSessionIntent)
                else None
            ),
        )

    vibe_acp_agent = VibeAcpAgent(session_starter=start_session)
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
