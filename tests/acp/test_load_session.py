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
from vibe.acp.user_display_content import USER_DISPLAY_CONTENT_META_KEY
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import ModelConfig, SessionLoggingConfig
from vibe.core.trusted_folders import trusted_folders_manager
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
        assert len(response.config_options) == 3
        assert response.config_options[0].id == "mode"
        assert response.config_options[0].category == "mode"
        assert response.config_options[0].current_value == BuiltinAgentName.DEFAULT
        assert len(response.config_options[0].options) == 5
        mode_option_values = {opt.value for opt in response.config_options[0].options}
        assert mode_option_values == {
            BuiltinAgentName.DEFAULT,
            BuiltinAgentName.CHAT,
            BuiltinAgentName.AUTO_APPROVE,
            BuiltinAgentName.PLAN,
            BuiltinAgentName.ACCEPT_EDITS,
        }
        assert response.config_options[1].id == "model"
        assert response.config_options[1].category == "model"
        assert response.config_options[1].current_value == "devstral-latest"
        assert len(response.config_options[1].options) == 2
        model_option_values = {opt.value for opt in response.config_options[1].options}
        assert model_option_values == {"devstral-latest", "devstral-small"}
        assert response.config_options[2].id == "thinking"
        assert response.config_options[2].category == "thinking"
        assert response.config_options[2].current_value == "off"

    @pytest.mark.asyncio
    async def test_load_session_returns_trust_details_without_loading_project_docs(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
        tmp_working_directory: Path,
    ) -> None:
        acp_agent, _client = acp_agent_with_session_config
        (tmp_working_directory / "AGENTS.md").write_text(
            "Loaded session project instructions", encoding="utf-8"
        )
        session_id = "test-sess-trust"
        create_test_session(temp_session_dir, session_id, str(tmp_working_directory))

        response = await acp_agent.load_session(
            cwd=str(tmp_working_directory), mcp_servers=[], session_id=session_id
        )

        assert response is not None
        payload = response.model_dump(mode="json", by_alias=True)
        assert payload.get("_meta") == {
            "workspace_trust": {
                "status": "untrusted",
                "details": {
                    "cwd": str(tmp_working_directory.resolve()),
                    "repoRoot": None,
                    "ignoredFiles": ["AGENTS.md"],
                    "availableDecisions": ["trust_cwd", "decline"],
                },
            }
        }
        assert trusted_folders_manager.is_trusted(tmp_working_directory) is None
        await acp_agent.sessions[session_id].agent_loop.wait_until_ready()
        system_prompt = acp_agent.sessions[session_id].agent_loop.messages[0].content
        assert system_prompt is not None
        assert "Loaded session project instructions" not in system_prompt

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
        assert acp_agent.sessions[session_id].agent_loop.session_id == session_id

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
        assert user_updates[0].update.field_meta is None

    @pytest.mark.asyncio
    async def test_load_session_replays_user_display_content(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "replay-dsp-123456"
        cwd = str(Path.cwd())
        user_display_content = {
            "version": "1.0.0",
            "host": "mistral-vscode",
            "content": [
                {"type": "text", "text": "Look at "},
                {
                    "type": "workspace_mention",
                    "kind": "file",
                    "uri": "file:///repo/src/app.ts",
                    "name": "app.ts",
                },
            ],
        }
        messages = [
            {
                "role": "user",
                "content": "Look at app.ts",
                "user_display_content": user_display_content,
            }
        ]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        user_updates = [
            u for u in client._session_updates if isinstance(u.update, UserMessageChunk)
        ]
        assert len(user_updates) == 1
        assert user_updates[0].update.content.text == "Look at app.ts"
        assert user_updates[0].update.field_meta == {
            USER_DISPLAY_CONTENT_META_KEY: user_display_content
        }

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
                            "name": "read",
                            "arguments": '{"file_path": "/tmp/test.txt"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_123",
                "name": "read",
                "content": "file contents",
            },
        ]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        tool_call_starts = [
            u for u in client._session_updates if isinstance(u.update, ToolCallStart)
        ]
        assert len(tool_call_starts) == 1
        start = tool_call_starts[0].update
        assert start.tool_call_id == "call_123"
        assert start.kind == "read"
        # The host drops created events without a status; replayed calls are
        # historical, so they must carry one.
        assert start.status == "completed"
        assert start.field_meta is not None
        assert start.field_meta["tool_name"] == "read"

        tool_results = [
            u for u in client._session_updates if isinstance(u.update, ToolCallProgress)
        ]
        assert len(tool_results) == 1
        result = tool_results[0].update
        assert result.tool_call_id == "call_123"
        assert result.status == "completed"
        # The host drops tool_call_update events without a kind.
        assert result.kind == "read"
        assert result.field_meta is not None
        assert result.field_meta["tool_name"] == "read"

    @pytest.mark.asyncio
    async def test_load_session_skips_result_whose_call_was_not_replayed(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "replay-orphan-12345"
        cwd = str(Path.cwd())
        # A tool result whose call was not replayed (here no matching tool call
        # at all, mirroring a hidden tool whose call replay returns None).
        # Emitting it would orphan a tool_call_update with no preceding call.
        messages = [
            {"role": "user", "content": "Do something"},
            {
                "role": "tool",
                "tool_call_id": "call_orphan",
                "name": "read",
                "content": "file contents",
            },
        ]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        tool_results = [
            u for u in client._session_updates if isinstance(u.update, ToolCallProgress)
        ]
        assert tool_results == []

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
    async def test_load_session_replays_reasoning_before_assistant_message(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "replay-order-1234"
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

        response_updates = [
            update.update
            for update in client._session_updates
            if isinstance(update.update, (AgentThoughtChunk, AgentMessageChunk))
        ]

        assert [type(update) for update in response_updates] == [
            AgentThoughtChunk,
            AgentMessageChunk,
        ]
        assert response_updates[0].content.text == "Let me think step by step..."
        assert response_updates[1].content.text == "Here is my answer"

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

    @pytest.mark.asyncio
    async def test_load_session_restores_agent_loop_session_identity(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, _client = acp_agent_with_session_config

        session_id = "restore-id-12345678"
        parent_session_id = "parent-id-87654321"
        cwd = str(Path.cwd())
        session_dir = create_test_session(
            temp_session_dir, session_id, cwd, parent_session_id=parent_session_id
        )

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        agent_loop = acp_agent.sessions[session_id].agent_loop

        assert agent_loop.session_id == session_id
        assert agent_loop.parent_session_id == parent_session_id
        assert agent_loop.session_logger.session_id == session_id
        assert agent_loop.session_logger.session_dir == session_dir
        assert agent_loop.session_logger.session_metadata is not None
        assert (
            agent_loop.session_logger.session_metadata.parent_session_id
            == parent_session_id
        )

    @pytest.mark.asyncio
    async def test_replay_user_message_has_message_id(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "msg-id-usr-1234567"
        cwd = str(Path.cwd())
        message_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        messages = [{"role": "user", "content": "Hello", "message_id": message_id}]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        user_updates = [
            u for u in client._session_updates if isinstance(u.update, UserMessageChunk)
        ]
        assert len(user_updates) == 1
        assert user_updates[0].update.message_id == message_id

    @pytest.mark.asyncio
    async def test_replay_agent_message_has_message_id(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "msg-id-ast-1234567"
        cwd = str(Path.cwd())
        message_id = "11111111-2222-3333-4444-555555555555"
        messages = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!", "message_id": message_id},
        ]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        agent_updates = [
            u
            for u in client._session_updates
            if isinstance(u.update, AgentMessageChunk)
        ]
        assert len(agent_updates) == 1
        assert agent_updates[0].update.message_id == message_id

    @pytest.mark.asyncio
    async def test_replay_reasoning_has_different_message_id_than_agent_message(
        self,
        acp_agent_with_session_config: tuple[VibeAcpAgentLoop, FakeClient],
        temp_session_dir: Path,
        create_test_session,
    ) -> None:
        acp_agent, client = acp_agent_with_session_config

        session_id = "msg-id-rsn-1234567"
        cwd = str(Path.cwd())
        agent_message_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        reasoning_message_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        messages = [
            {"role": "user", "content": "Think about this"},
            {
                "role": "assistant",
                "content": "Here is my answer",
                "message_id": agent_message_id,
                "reasoning_content": "Let me think...",
                "reasoning_message_id": reasoning_message_id,
            },
        ]
        create_test_session(temp_session_dir, session_id, cwd, messages=messages)

        await acp_agent.load_session(cwd=cwd, mcp_servers=[], session_id=session_id)

        agent_updates = [
            u
            for u in client._session_updates
            if isinstance(u.update, AgentMessageChunk)
        ]
        thought_updates = [
            u
            for u in client._session_updates
            if isinstance(u.update, AgentThoughtChunk)
        ]
        assert len(agent_updates) == 1
        assert len(thought_updates) == 1
        assert agent_updates[0].update.message_id == agent_message_id
        assert thought_updates[0].update.message_id == reasoning_message_id
        assert (
            agent_updates[0].update.message_id != thought_updates[0].update.message_id
        )
