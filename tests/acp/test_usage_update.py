from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from acp.schema import TextContentBlock, UsageUpdate
import pytest

from tests.acp.conftest import _create_acp_agent
from tests.conftest import build_test_vibe_config
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_client import FakeClient
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agent_loop import AgentLoop
from vibe.core.config import SessionLoggingConfig
from vibe.core.types import LLMChunk, LLMMessage, LLMUsage, Role


def _make_backend(prompt_tokens: int = 100, completion_tokens: int = 50) -> FakeBackend:
    return FakeBackend(
        LLMChunk(
            message=LLMMessage(role=Role.assistant, content="Hi"),
            usage=LLMUsage(
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
            ),
        )
    )


def _make_acp_agent(backend: FakeBackend) -> VibeAcpAgentLoop:
    config = build_test_vibe_config()

    class PatchedAgentLoop(AgentLoop):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **{**kwargs, "backend": backend})
            self._base_config = config
            self.agent_manager.invalidate_config()

    patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=PatchedAgentLoop).start()
    return _create_acp_agent()


def _get_fake_client(agent: VibeAcpAgentLoop) -> FakeClient:
    return agent.client  # type: ignore[return-value]


def _get_usage_updates(client: FakeClient) -> list[UsageUpdate]:
    return [
        update.update
        for update in client._session_updates
        if isinstance(update.update, UsageUpdate)
    ]


class TestPromptResponseUsage:
    @pytest.mark.asyncio
    async def test_prompt_returns_usage_in_response(self) -> None:
        agent = _make_acp_agent(_make_backend(prompt_tokens=100, completion_tokens=50))
        session = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        response = await agent.prompt(
            session_id=session.session_id,
            prompt=[TextContentBlock(type="text", text="Hello")],
        )

        assert response.usage is not None
        assert response.usage.input_tokens == 100
        assert response.usage.output_tokens == 50
        assert response.usage.total_tokens == 150

    @pytest.mark.asyncio
    async def test_prompt_usage_optional_fields_are_none(self) -> None:
        agent = _make_acp_agent(_make_backend())
        session = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        response = await agent.prompt(
            session_id=session.session_id,
            prompt=[TextContentBlock(type="text", text="Hello")],
        )

        assert response.usage is not None
        assert response.usage.thought_tokens is None
        assert response.usage.cached_read_tokens is None
        assert response.usage.cached_write_tokens is None

    @pytest.mark.asyncio
    async def test_prompt_usage_accumulates_across_turns(self) -> None:
        backend = _make_backend(prompt_tokens=100, completion_tokens=50)
        agent = _make_acp_agent(backend)
        session = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        first = await agent.prompt(
            session_id=session.session_id,
            prompt=[TextContentBlock(type="text", text="Hello")],
        )

        second = await agent.prompt(
            session_id=session.session_id,
            prompt=[TextContentBlock(type="text", text="Hello again")],
        )

        assert first.usage is not None
        assert second.usage is not None
        # Second turn should have strictly more cumulative tokens
        assert second.usage.input_tokens > first.usage.input_tokens
        assert second.usage.output_tokens > first.usage.output_tokens
        assert second.usage.total_tokens > first.usage.total_tokens


class TestUsageUpdateNotification:
    @pytest.mark.asyncio
    async def test_prompt_sends_usage_update(self) -> None:
        agent = _make_acp_agent(_make_backend())
        session = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        await agent.prompt(
            session_id=session.session_id,
            prompt=[TextContentBlock(type="text", text="Hello")],
        )
        await asyncio.sleep(0)

        usage_updates = _get_usage_updates(_get_fake_client(agent))
        assert len(usage_updates) == 1
        assert usage_updates[0].session_update == "usage_update"

    @pytest.mark.asyncio
    async def test_usage_update_contains_context_window_info(self) -> None:
        agent = _make_acp_agent(_make_backend(prompt_tokens=100, completion_tokens=50))
        session = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        await agent.prompt(
            session_id=session.session_id,
            prompt=[TextContentBlock(type="text", text="Hello")],
        )
        await asyncio.sleep(0)

        usage_updates = _get_usage_updates(_get_fake_client(agent))
        assert len(usage_updates) == 1
        assert usage_updates[0].size > 0
        assert usage_updates[0].used > 0

    @pytest.mark.asyncio
    async def test_usage_update_contains_cost_when_pricing_set(self) -> None:
        agent = _make_acp_agent(
            _make_backend(prompt_tokens=1_000_000, completion_tokens=500_000)
        )
        session = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        # Set pricing directly on the session stats (config loading uses fixture defaults)
        acp_session = agent.sessions[session.session_id]
        acp_session.agent_loop.stats.update_pricing(input_price=0.4, output_price=2.0)

        await agent.prompt(
            session_id=session.session_id,
            prompt=[TextContentBlock(type="text", text="Hello")],
        )
        await asyncio.sleep(0)

        usage_updates = _get_usage_updates(_get_fake_client(agent))
        assert len(usage_updates) == 1
        cost = usage_updates[0].cost
        assert cost is not None
        assert cost.currency == "USD"
        assert cost.amount > 0

    @pytest.mark.asyncio
    async def test_usage_update_no_cost_when_zero_pricing(self) -> None:
        agent = _make_acp_agent(_make_backend())
        session = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        await agent.prompt(
            session_id=session.session_id,
            prompt=[TextContentBlock(type="text", text="Hello")],
        )
        await asyncio.sleep(0)

        usage_updates = _get_usage_updates(_get_fake_client(agent))
        assert len(usage_updates) == 1
        assert usage_updates[0].cost is None

    @pytest.mark.asyncio
    async def test_usage_update_sent_per_prompt(self) -> None:
        backend = _make_backend()
        agent = _make_acp_agent(backend)
        session = await agent.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        await agent.prompt(
            session_id=session.session_id,
            prompt=[TextContentBlock(type="text", text="Hello")],
        )
        await asyncio.sleep(0)
        await agent.prompt(
            session_id=session.session_id,
            prompt=[TextContentBlock(type="text", text="Hello again")],
        )
        await asyncio.sleep(0)

        usage_updates = _get_usage_updates(_get_fake_client(agent))
        assert len(usage_updates) == 2


class TestLoadSessionUsageUpdate:
    def _make_session_dir(self, tmp_path: Path, session_id: str, cwd: str) -> Path:
        session_folder = tmp_path / f"session_20240101_120000_{session_id[:8]}"
        session_folder.mkdir()
        messages_file = session_folder / "messages.jsonl"
        with messages_file.open("w") as f:
            f.write(json.dumps({"role": "user", "content": "Hello"}) + "\n")
        meta = {
            "session_id": session_id,
            "start_time": "2024-01-01T12:00:00Z",
            "end_time": "2024-01-01T12:05:00Z",
            "environment": {"working_directory": cwd},
        }
        with (session_folder / "meta.json").open("w") as f:
            json.dump(meta, f)
        return session_folder

    def _make_agent_with_session_logging(
        self, backend: FakeBackend, session_dir: Path
    ) -> VibeAcpAgentLoop:
        session_config = SessionLoggingConfig(
            save_dir=str(session_dir), session_prefix="session", enabled=True
        )
        config = build_test_vibe_config(session_logging=session_config)

        class PatchedAgentLoop(AgentLoop):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **{**kwargs, "backend": backend})
                self._base_config = config
                self.agent_manager.invalidate_config()

        patch("vibe.acp.acp_agent_loop.AgentLoop", side_effect=PatchedAgentLoop).start()
        agent = _create_acp_agent()
        patch.object(agent, "_load_config", return_value=config).start()
        return agent

    @pytest.mark.asyncio
    async def test_load_session_sends_usage_update(self, tmp_path: Path) -> None:
        backend = _make_backend()
        agent = self._make_agent_with_session_logging(backend, tmp_path)
        session_id = "test-session-load-usage"
        self._make_session_dir(tmp_path, session_id, str(Path.cwd()))

        await agent.load_session(cwd=str(Path.cwd()), session_id=session_id)
        await asyncio.sleep(0)

        client = _get_fake_client(agent)
        usage_updates = _get_usage_updates(client)
        assert len(usage_updates) == 1
        assert usage_updates[0].session_update == "usage_update"
        assert usage_updates[0].size > 0
