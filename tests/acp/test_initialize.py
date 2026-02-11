from __future__ import annotations

from acp import PROTOCOL_VERSION
from acp.schema import (
    AgentCapabilities,
    ClientCapabilities,
    Implementation,
    PromptCapabilities,
)
import pytest

from vibe.acp.acp_agent_loop import VibeAcpAgentLoop


class TestACPInitialize:
    @pytest.mark.asyncio
    async def test_initialize(self, acp_agent_loop: VibeAcpAgentLoop) -> None:
        response = await acp_agent_loop.initialize(protocol_version=PROTOCOL_VERSION)

        assert response.protocol_version == PROTOCOL_VERSION
        assert response.agent_capabilities == AgentCapabilities(
            load_session=False,
            prompt_capabilities=PromptCapabilities(
                audio=False, embedded_context=True, image=False
            ),
        )
        assert response.agent_info == Implementation(
            name="@mistralai/mistral-vibe", title="Mistral Vibe", version="2.1.0"
        )

        assert response.auth_methods == []

    @pytest.mark.asyncio
    async def test_initialize_with_terminal_auth(
        self, acp_agent_loop: VibeAcpAgentLoop
    ) -> None:
        """Test initialize with terminal-auth capabilities to check it was included."""
        client_capabilities = ClientCapabilities(field_meta={"terminal-auth": True})
        response = await acp_agent_loop.initialize(
            protocol_version=PROTOCOL_VERSION, client_capabilities=client_capabilities
        )

        assert response.protocol_version == PROTOCOL_VERSION
        assert response.agent_capabilities == AgentCapabilities(
            load_session=False,
            prompt_capabilities=PromptCapabilities(
                audio=False, embedded_context=True, image=False
            ),
        )
        assert response.agent_info == Implementation(
            name="@mistralai/mistral-vibe", title="Mistral Vibe", version="2.1.0"
        )

        assert response.auth_methods is not None
        assert len(response.auth_methods) == 1
        auth_method = response.auth_methods[0]
        assert auth_method.id == "vibe-setup"
        assert auth_method.name == "Register your API Key"
        assert auth_method.description == "Register your API Key inside Mistral Vibe"
        assert auth_method.field_meta is not None
        assert "terminal-auth" in auth_method.field_meta
        terminal_auth_meta = auth_method.field_meta["terminal-auth"]
        assert "command" in terminal_auth_meta
        assert "args" in terminal_auth_meta
        assert terminal_auth_meta["label"] == "Mistral Vibe Setup"
