from __future__ import annotations

from acp import PROTOCOL_VERSION, AgentSideConnection, InitializeRequest
from acp.schema import (
    AgentCapabilities,
    ClientCapabilities,
    Implementation,
    PromptCapabilities,
)
import pytest

from tests.stubs.fake_connection import FakeAgentSideConnection
from vibe.acp.acp_agent import VibeAcpAgent


@pytest.fixture
def acp_agent() -> VibeAcpAgent:
    vibe_acp_agent: VibeAcpAgent | None = None

    def _create_agent(connection: AgentSideConnection) -> VibeAcpAgent:
        nonlocal vibe_acp_agent
        vibe_acp_agent = VibeAcpAgent(connection)
        return vibe_acp_agent

    FakeAgentSideConnection(_create_agent)
    return vibe_acp_agent  # pyright: ignore[reportReturnType]


class TestACPInitialize:
    @pytest.mark.asyncio
    async def test_initialize(self, acp_agent: VibeAcpAgent) -> None:
        """Test regular initialize without terminal-auth capabilities."""
        request = InitializeRequest(protocolVersion=PROTOCOL_VERSION)
        response = await acp_agent.initialize(request)

        assert response.protocolVersion == PROTOCOL_VERSION
        assert response.agentCapabilities == AgentCapabilities(
            loadSession=False,
            promptCapabilities=PromptCapabilities(
                audio=False, embeddedContext=True, image=False
            ),
        )
        assert response.agentInfo == Implementation(
            name="@mistralai/mistral-vibe", title="Mistral Vibe", version="1.3.4"
        )

        assert response.authMethods == []

    @pytest.mark.asyncio
    async def test_initialize_with_terminal_auth(self, acp_agent: VibeAcpAgent) -> None:
        """Test initialize with terminal-auth capabilities to check it was included."""
        client_capabilities = ClientCapabilities(field_meta={"terminal-auth": True})
        request = InitializeRequest(
            protocolVersion=PROTOCOL_VERSION, clientCapabilities=client_capabilities
        )
        response = await acp_agent.initialize(request)

        assert response.protocolVersion == PROTOCOL_VERSION
        assert response.agentCapabilities == AgentCapabilities(
            loadSession=False,
            promptCapabilities=PromptCapabilities(
                audio=False, embeddedContext=True, image=False
            ),
        )
        assert response.agentInfo == Implementation(
            name="@mistralai/mistral-vibe", title="Mistral Vibe", version="1.3.4"
        )

        assert response.authMethods is not None
        assert len(response.authMethods) == 1
        auth_method = response.authMethods[0]
        assert auth_method.id == "vibe-setup"
        assert auth_method.name == "Register your API Key"
        assert auth_method.description == "Register your API Key inside Mistral Vibe"
        assert auth_method.field_meta is not None
        assert "terminal-auth" in auth_method.field_meta
        terminal_auth_meta = auth_method.field_meta["terminal-auth"]
        assert "command" in terminal_auth_meta
        assert "args" in terminal_auth_meta
        assert terminal_auth_meta["label"] == "Mistral Vibe Setup"
