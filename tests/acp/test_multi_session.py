from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from acp import PROTOCOL_VERSION, RequestError
from acp.schema import TextContentBlock
import pytest
from pytest import raises

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.types import Role


class TestMultiSessionCore:
    @pytest.mark.asyncio
    async def test_different_sessions_use_different_agents(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        await acp_agent_loop.initialize(protocol_version=PROTOCOL_VERSION)
        session1_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session1 = acp_agent_loop.sessions[session1_response.session_id]
        session2_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session2 = acp_agent_loop.sessions[session2_response.session_id]

        assert session1.id != session2.id
        # Each agent loop should be independent
        assert session1.agent_loop is not session2.agent_loop
        assert id(session1.agent_loop) != id(session2.agent_loop)

    @pytest.mark.asyncio
    async def test_error_on_nonexistent_session(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        await acp_agent_loop.initialize(protocol_version=PROTOCOL_VERSION)
        await acp_agent_loop.new_session(cwd=str(Path.cwd()), mcp_servers=[])

        fake_session_id = "fake-session-id-" + str(uuid4())

        with raises(RequestError) as exc_info:
            await acp_agent_loop.prompt(
                session_id=fake_session_id,
                prompt=[TextContentBlock(type="text", text="Hello, world!")],
            )

        assert isinstance(exc_info.value, RequestError)
        assert str(exc_info.value) == "Invalid params"

    @pytest.mark.asyncio
    async def test_simultaneous_message_processing(
        self, acp_agent_loop: VibeAcpAgentLoop, backend: FakeBackend
    ) -> None:
        await acp_agent_loop.initialize(protocol_version=PROTOCOL_VERSION)
        session1_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session1 = acp_agent_loop.sessions[session1_response.session_id]
        session2_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session2 = acp_agent_loop.sessions[session2_response.session_id]

        backend._streams = [
            [mock_llm_chunk(content="Response 1")],
            [mock_llm_chunk(content="Response 2")],
        ]

        async def run_session1():
            await acp_agent_loop.prompt(
                session_id=session1.id,
                prompt=[TextContentBlock(type="text", text="Prompt for session 1")],
            )

        async def run_session2():
            await acp_agent_loop.prompt(
                session_id=session2.id,
                prompt=[TextContentBlock(type="text", text="Prompt for session 2")],
            )

        await asyncio.gather(run_session1(), run_session2())

        user_message1 = next(
            (msg for msg in session1.agent_loop.messages if msg.role == Role.user), None
        )
        assert user_message1 is not None
        assert user_message1.content == "Prompt for session 1"
        assistant_message1 = next(
            (msg for msg in session1.agent_loop.messages if msg.role == Role.assistant),
            None,
        )
        assert assistant_message1 is not None
        assert assistant_message1.content == "Response 1"
        user_message2 = next(
            (msg for msg in session2.agent_loop.messages if msg.role == Role.user), None
        )
        assert user_message2 is not None
        assert user_message2.content == "Prompt for session 2"
        assistant_message2 = next(
            (msg for msg in session2.agent_loop.messages if msg.role == Role.assistant),
            None,
        )
        assert assistant_message2 is not None
        assert assistant_message2.content == "Response 2"
