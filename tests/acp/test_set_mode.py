from __future__ import annotations

from pathlib import Path

import pytest

from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.agents.models import BuiltinAgentName


class TestACPSetMode:
    @pytest.mark.asyncio
    async def test_set_mode_to_default(self, acp_agent_loop: VibeAcpAgentLoop) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        await acp_session.agent_loop.switch_agent(BuiltinAgentName.AUTO_APPROVE)

        response = await acp_agent_loop.set_session_mode(
            session_id=session_id, mode_id=BuiltinAgentName.DEFAULT
        )

        assert response is not None
        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.DEFAULT
        assert acp_session.agent_loop.auto_approve is False

    @pytest.mark.asyncio
    async def test_set_mode_to_auto_approve(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.DEFAULT
        assert acp_session.agent_loop.auto_approve is False

        response = await acp_agent_loop.set_session_mode(
            session_id=session_id, mode_id=BuiltinAgentName.AUTO_APPROVE
        )

        assert response is not None
        assert (
            acp_session.agent_loop.agent_profile.name == BuiltinAgentName.AUTO_APPROVE
        )
        assert acp_session.agent_loop.auto_approve is True

    @pytest.mark.asyncio
    async def test_set_mode_to_plan(self, acp_agent_loop: VibeAcpAgentLoop) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.DEFAULT

        response = await acp_agent_loop.set_session_mode(
            session_id=session_id, mode_id=BuiltinAgentName.PLAN
        )

        assert response is not None
        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.PLAN
        assert (
            acp_session.agent_loop.auto_approve is True
        )  # Plan mode auto-approves read-only tools

    @pytest.mark.asyncio
    async def test_set_mode_to_accept_edits(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.DEFAULT

        response = await acp_agent_loop.set_session_mode(
            session_id=session_id, mode_id=BuiltinAgentName.ACCEPT_EDITS
        )

        assert response is not None
        assert (
            acp_session.agent_loop.agent_profile.name == BuiltinAgentName.ACCEPT_EDITS
        )
        assert (
            acp_session.agent_loop.auto_approve is False
        )  # Accept Edits mode doesn't auto-approve all

    @pytest.mark.asyncio
    async def test_set_mode_to_chat(self, acp_agent_loop: VibeAcpAgentLoop) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.DEFAULT

        response = await acp_agent_loop.set_session_mode(
            session_id=session_id, mode_id=BuiltinAgentName.CHAT
        )

        assert response is not None
        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.CHAT
        assert (
            acp_session.agent_loop.auto_approve is True
        )  # Chat mode auto-approves read-only tools

    @pytest.mark.asyncio
    async def test_set_mode_invalid_mode_returns_none(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        initial_agent = acp_session.agent_loop.agent_profile.name
        initial_auto_approve = acp_session.agent_loop.auto_approve

        response = await acp_agent_loop.set_session_mode(
            session_id=session_id, mode_id="invalid-mode"
        )

        assert response is None
        assert acp_session.agent_loop.agent_profile.name == initial_agent
        assert acp_session.agent_loop.auto_approve == initial_auto_approve

    @pytest.mark.asyncio
    async def test_set_mode_to_same_mode(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.DEFAULT

        response = await acp_agent_loop.set_session_mode(
            session_id=session_id, mode_id=BuiltinAgentName.DEFAULT
        )

        assert response is not None
        assert acp_session.agent_loop.agent_profile.name == BuiltinAgentName.DEFAULT
        assert acp_session.agent_loop.auto_approve is False

    @pytest.mark.asyncio
    async def test_set_mode_with_empty_string(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        session_response = await acp_agent_loop.new_session(
            cwd=str(Path.cwd()), mcp_servers=[]
        )
        session_id = session_response.session_id
        acp_session = next(
            (s for s in acp_agent_loop.sessions.values() if s.id == session_id), None
        )
        assert acp_session is not None

        initial_agent = acp_session.agent_loop.agent_profile.name
        initial_auto_approve = acp_session.agent_loop.auto_approve

        response = await acp_agent_loop.set_session_mode(
            session_id=session_id, mode_id=""
        )

        assert response is None
        assert acp_session.agent_loop.agent_profile.name == initial_agent
        assert acp_session.agent_loop.auto_approve == initial_auto_approve
