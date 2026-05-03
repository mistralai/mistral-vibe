from __future__ import annotations

import asyncio
from pathlib import Path

from acp.schema import TextContentBlock
import pytest

from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.exceptions import InvalidRequestError
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.session.session_id import extract_suffix
from vibe.core.types import FunctionCall, LLMMessage, Role, ToolCall


class TestACPForkSession:
    @pytest.mark.asyncio
    async def test_fork_session_clones_history_and_mode(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        source_session = acp_agent_loop.sessions[session_response.session_id]

        await acp_agent_loop.set_session_mode(
            session_id=source_session.id, mode_id=BuiltinAgentName.PLAN
        )
        await acp_agent_loop.prompt(
            prompt=[TextContentBlock(type="text", text="Say hi")],
            session_id=source_session.id,
        )

        response = await acp_agent_loop.fork_session(
            cwd=str(Path.cwd()), session_id=source_session.id, mcp_servers=[]
        )

        assert response.session_id != source_session.id
        assert response.modes is not None
        assert response.modes.current_mode_id == BuiltinAgentName.PLAN
        assert response.models is not None
        assert (
            response.models.current_model_id
            == source_session.agent_loop.config.active_model
        )
        assert response.config_options is not None
        assert len(response.config_options) == 3

        forked_session = acp_agent_loop.sessions[response.session_id]
        assert forked_session.agent_loop.agent_profile.name == BuiltinAgentName.PLAN

        source_messages = [
            message.model_dump(mode="json", exclude_none=True)
            for message in source_session.agent_loop.messages
            if message.role != Role.system
        ]
        forked_messages = [
            message.model_dump(mode="json", exclude_none=True)
            for message in forked_session.agent_loop.messages
            if message.role != Role.system
        ]
        assert forked_messages == source_messages

    @pytest.mark.asyncio
    async def test_fork_session_from_user_message_keeps_full_turn(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        source_session = acp_agent_loop.sessions[session_response.session_id]
        source_session.agent_loop.messages.extend([
            LLMMessage(role=Role.user, content="First", message_id="user-1"),
            LLMMessage(
                role=Role.assistant, content="First answer", message_id="assistant-1"
            ),
            LLMMessage(
                role=Role.assistant,
                content="",
                message_id="assistant-2",
                tool_calls=[
                    ToolCall(
                        id="call-1",
                        index=0,
                        function=FunctionCall(
                            name="read_file", arguments='{"path":"a.txt"}'
                        ),
                    )
                ],
            ),
            LLMMessage(role=Role.tool, content="contents", tool_call_id="call-1"),
            LLMMessage(role=Role.user, content="Second", message_id="user-2"),
            LLMMessage(
                role=Role.assistant, content="Second answer", message_id="assistant-3"
            ),
        ])

        response = await acp_agent_loop.fork_session(
            cwd=str(Path.cwd()),
            session_id=source_session.id,
            mcp_servers=[],
            messageId="user-1",
        )

        forked_session = acp_agent_loop.sessions[response.session_id]
        assert [
            (message.role, message.content, message.message_id, message.tool_call_id)
            for message in forked_session.agent_loop.messages
            if message.role != Role.system
        ] == [
            (Role.user, "First", "user-1", None),
            (Role.assistant, "First answer", "assistant-1", None),
            (Role.assistant, "", "assistant-2", None),
            (Role.tool, "contents", None, "call-1"),
        ]

    @pytest.mark.asyncio
    async def test_fork_session_rejects_non_user_message_id(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        source_session = acp_agent_loop.sessions[session_response.session_id]
        source_session.agent_loop.messages.extend([
            LLMMessage(role=Role.user, content="First", message_id="user-1"),
            LLMMessage(
                role=Role.assistant, content="First answer", message_id="assistant-1"
            ),
        ])

        with pytest.raises(
            InvalidRequestError,
            match="Fork from message_id is only supported for user messages",
        ):
            await acp_agent_loop.fork_session(
                cwd=str(Path.cwd()),
                session_id=source_session.id,
                mcp_servers=[],
                messageId="assistant-1",
            )

    @pytest.mark.asyncio
    async def test_fork_session_sets_parent_session_id(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        source_session = acp_agent_loop.sessions[session_response.session_id]

        response = await acp_agent_loop.fork_session(
            cwd=str(Path.cwd()), session_id=source_session.id, mcp_servers=[]
        )

        forked_session = acp_agent_loop.sessions[response.session_id]
        assert forked_session.agent_loop.parent_session_id == source_session.id

    @pytest.mark.asyncio
    async def test_fork_session_rejects_running_session(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        source_session = acp_agent_loop.sessions[session_response.session_id]
        source_session.set_prompt_task(asyncio.sleep(10))

        with pytest.raises(
            InvalidRequestError,
            match="Cannot fork a session while the agent loop is running",
        ):
            await acp_agent_loop.fork_session(
                cwd=str(Path.cwd()), session_id=source_session.id, mcp_servers=[]
            )

        await source_session.cancel_prompt()

    @pytest.mark.asyncio
    async def test_fork_session_preserves_session_id_suffix(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        source_session = acp_agent_loop.sessions[session_response.session_id]

        response = await acp_agent_loop.fork_session(
            cwd=str(Path.cwd()), session_id=source_session.id, mcp_servers=[]
        )

        assert extract_suffix(response.session_id) == extract_suffix(source_session.id)
