from __future__ import annotations

from pathlib import Path

from acp import RequestError
from acp.schema import (
    AgentMessageChunk,
    AgentThoughtChunk,
    ToolCallProgress,
    ToolCallStart,
    UserMessageChunk,
)
import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import ModelConfig, SessionLoggingConfig
from vibe.core.types import Role


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
            ),
            ModelConfig(
                name="devstral-small", provider="mistral", alias="devstral-small"
            ),
        ],
        session_logging=session_config,
    )

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **{**kwargs, "backend": backend})
            self._base_config = config
            self.agent_manager.invalidate_config()

    monkeypatch.setattr("vibe.acp.acp_agent_loop.AgentLoop", PatchedAgentLoop)
    monkeypatch.setattr(VibeAcpAgentLoop, "_load_config", lambda self: config)

    vibe_acp_agent = VibeAcpAgentLoop()
    client = FakeClient()
    vibe_acp_agent.on_connect(client)
    client.on_connect(vibe_acp_agent)

    return vibe_acp_agent, client


class TestLoadSession:
    @pytest.mark.asyncio
    async def test_load_session_response_structure(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, _client = acp_agent_with_session_config

        session_id = "test-sess-12345678"
        cwd = str(Path.cwd())
        create_test_session(temp_session_dir, session_id, cwd)

        response = await acp_agent.load_session(
            cwd=cwd, mcp_servers=[], session_id=session_id
        )

        assert response is not None
        assert response.models is not None
        assert len(response.models.available_models) == 2

        assert response.models.current_model_id == "devstral-latest"
        assert response.models.available_models[0].model_id == "devstral-latest"
        assert response.models.available_models[0].name == "devstral-latest"
        assert response.models.available_models[1].model_id == "devstral-small"
        assert response.models.available_models[1].name == "devstral-small"

        assert response.modes is not None
        assert response.modes.current_mode_id == BuiltinAgentName.DEFAULT
        modes_ids = {m.id for m in response.modes.available_modes}
        assert modes_ids == {
            BuiltinAgentName.DEFAULT,
            BuiltinAgentName.CHAT,
            BuiltinAgentName.AUTO_APPROVE,
            BuiltinAgentName.PLAN,
            BuiltinAgentName.ACCEPT_EDITS,
        }

        assert response.config_options is not None
        assert len(response.config_options) == 2
        assert response.config_options[0].root.id == "mode"
        assert response.config_options[0].root.category == "mode"
        assert response.config_options[0].root.current_value == BuiltinAgentName.DEFAULT
        assert len(response.config_options[0].root.options) == 5
        mode_option_values = {
            opt.value for opt in response.config_options[0].root.options
        }
        assert mode_option_values == {
            BuiltinAgentName.DEFAULT,
            BuiltinAgentName.CHAT,
            BuiltinAgentName.AUTO_APPROVE,
            BuiltinAgentName.PLAN,
            BuiltinAgentName.ACCEPT_EDITS,
        }
        assert response.config_options[1].root.id == "model"
        assert response.config_options[1].root.category == "model"
        assert response.config_options[1].root.current_value == "devstral-latest"
        assert len(response.config_options[1].root.options) == 2
        model_option_values = {
            opt.value for opt in response.config_options[1].root.options
        }
        assert model_option_values == {"devstral-latest", "devstral-small"}

    @pytest.mark.asyncio
    async def test_load_session_registers_session_with_original_id(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, _client = acp_agent_with_session_config

        session_id = "orig-id-12345678"
        cwd = str(Path.cwd())
        create_test_session(temp_session_dir, session_id, cwd)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        assert session_id in acp_agent.sessions
        assert acp_agent.sessions[session_id].id == session_id

    @pytest.mark.asyncio
    async def test_load_session_injects_messages_into_agent_loop(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, _client = acp_agent_with_session_config

        session_id = "msg-test-12345678"
        cwd = str(Path.cwd())
        messages = [
            {"role": "system", "content": "You are helpful"},
            {"role": "user", "content": "First question"},
            {"role": "assistant", "content": "First answer"},
            {"role": "user", "content": "Second question"},
            {"role": "assistant", "content": "Second answer"},
        ]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        session = acp_agent.sessions[session_id]

        non_system = [m for m in session.agent_loop.messages if m.role != Role.system]
        assert len(non_system) == 4

    @pytest.mark.asyncio
    async def test_load_session_replays_user_messages(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "replay-usr-123456"
        cwd = str(Path.cwd())
        messages = [{"role": "user", "content": "Hello world"}]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        user_updates = [
            u for u in client._session_updates if isinstance(u.update, UserMessageChunk)
        ]
        assert len(user_updates) == 1
        assert user_updates[0].update.content.text == "Hello world"

    @pytest.mark.asyncio
    async def test_load_session_replays_assistant_messages(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "replay-ast-123456"
        cwd = str(Path.cwd())
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello! How can I help?"},
        ]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        agent_updates = [
            u
            for u in client._session_updates
            if isinstance(u.update, AgentMessageChunk)
        ]
        assert len(agent_updates) == 1
        assert agent_updates[0].update.content.text == "Hello! How can I help?"

    @pytest.mark.asyncio
    async def test_load_session_replays_tool_calls(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "replay-tool-12345"
        cwd = str(Path.cwd())
        messages = [
            {"role": "user", "content": "Read the file"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": '{"path": "/tmp/test.txt"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_123", "content": "file contents"},
        ]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        tool_call_starts = [
            u for u in client._session_updates if isinstance(u.update, ToolCallStart)
        ]
        assert len(tool_call_starts) == 1
        assert tool_call_starts[0].update.title == "read_file"
        assert tool_call_starts[0].update.tool_call_id == "call_123"

        tool_results = [
            u for u in client._session_updates if isinstance(u.update, ToolCallProgress)
        ]
        assert len(tool_results) == 1
        assert tool_results[0].update.tool_call_id == "call_123"
        assert tool_results[0].update.status == "completed"

    @pytest.mark.asyncio
    async def test_load_session_replays_reasoning_content(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "replay-reason-123"
        cwd = str(Path.cwd())
        messages = [
            {"role": "user", "content": "Think about this"},
            {
                "role": "assistant",
                "content": "Here is my answer",
                "reasoning_content": "Let me think step by step...",
            },
        ]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        thought_updates = [
            u
            for u in client._session_updates
            if isinstance(u.update, AgentThoughtChunk)
        ]
        assert len(thought_updates) == 1
        assert thought_updates[0].update.content.text == "Let me think step by step..."

    @pytest.mark.asyncio
    async def test_load_session_not_found_raises_error(
        self, acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient]
    ) -> None:
        acp_agent, _client = acp_agent_with_session_config

        with pytest.raises(RequestError):
            await acp_agent.load_session(
                cwd=str(Path.cwd()), mcp_servers=[], session_id="nonexistent-session"
            )

    @pytest.mark.asyncio
    async def test_load_session_replays_full_conversation(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "full-conv-1234567"
        cwd = str(Path.cwd())
        messages = [
            {"role": "user", "content": "First message"},
            {"role": "assistant", "content": "First response"},
            {"role": "user", "content": "Second message"},
            {"role": "assistant", "content": "Second response"},
        ]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        user_updates = [
            u for u in client._session_updates if isinstance(u.update, UserMessageChunk)
        ]
        agent_updates = [
            u
            for u in client._session_updates
            if isinstance(u.update, AgentMessageChunk)
        ]

        assert len(user_updates) == 2
        assert len(agent_updates) == 2
        assert user_updates[0].update.content.text == "First message"
        assert user_updates[1].update.content.text == "Second message"
        assert agent_updates[0].update.content.text == "First response"
        assert agent_updates[1].update.content.text == "Second response"
