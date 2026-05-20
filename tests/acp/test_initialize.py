from __future__ import annotations

from acp import PROTOCOL_VERSION
from acp.schema import (
    AgentCapabilities,
    ClientCapabilities,
    Implementation,
    PromptCapabilities,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionForkCapabilities,
    SessionListCapabilities,
)
import pytest

from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.core.config import ProviderConfig
from vibe.core.types import Backend
from vibe.setup.onboarding.context import OnboardingContext

BROWSER_AUTH_NAME = "Sign in through Mistral AI Studio"
BROWSER_AUTH_DESCRIPTION = (
    "Sign into Mistral Vibe through your Mistral AI Studio account."
)


def build_mistral_provider() -> ProviderConfig:
    return ProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var="MISTRAL_API_KEY",
        browser_auth_base_url="https://console.mistral.ai",
        browser_auth_api_base_url="https://console.mistral.ai/api",
        backend=Backend.MISTRAL,
    )


def build_acp_agent_loop(
    *, provider: ProviderConfig | None = None, enable_browser_sign_in: bool = True
) -> VibeAcpAgentLoop:
    return VibeAcpAgentLoop(
        onboarding_context_loader=lambda: OnboardingContext(
            provider=provider or build_mistral_provider(),
            enable_experimental_browser_sign_in=enable_browser_sign_in,
        )
    )


class TestACPInitialize:
    @pytest.mark.asyncio
    async def test_initialize(self) -> None:
        acp_agent_loop = build_acp_agent_loop()
        response = await acp_agent_loop.initialize(protocol_version=PROTOCOL_VERSION)

        assert response.protocol_version == PROTOCOL_VERSION
        assert response.agent_capabilities == AgentCapabilities(
            load_session=True,
            prompt_capabilities=PromptCapabilities(
                audio=False, embedded_context=True, image=False
            ),
            session_capabilities=SessionCapabilities(
                close=SessionCloseCapabilities(),
                list=SessionListCapabilities(),
                fork=SessionForkCapabilities(),
            ),
        )
        assert response.agent_info == Implementation(
            name="@mistralai/mistral-vibe", title="Mistral Vibe", version="2.10.1"
        )

        assert response.auth_methods is not None
        assert len(response.auth_methods) == 1
        auth_method = response.auth_methods[0]
        assert auth_method.id == "browser-auth"
        assert auth_method.name == BROWSER_AUTH_NAME
        assert auth_method.description == BROWSER_AUTH_DESCRIPTION

    @pytest.mark.asyncio
    async def test_initialize_with_terminal_auth(self) -> None:
        """Test initialize with terminal-auth capabilities to check it was included."""
        acp_agent_loop = build_acp_agent_loop()
        client_capabilities = ClientCapabilities(field_meta={"terminal-auth": True})
        response = await acp_agent_loop.initialize(
            protocol_version=PROTOCOL_VERSION, client_capabilities=client_capabilities
        )

        assert response.protocol_version == PROTOCOL_VERSION
        assert response.agent_capabilities == AgentCapabilities(
            load_session=True,
            prompt_capabilities=PromptCapabilities(
                audio=False, embedded_context=True, image=False
            ),
            session_capabilities=SessionCapabilities(
                close=SessionCloseCapabilities(),
                list=SessionListCapabilities(),
                fork=SessionForkCapabilities(),
            ),
        )
        assert response.agent_info == Implementation(
            name="@mistralai/mistral-vibe", title="Mistral Vibe", version="2.10.1"
        )

        assert response.auth_methods is not None
        assert len(response.auth_methods) == 2

        browser_auth_method = response.auth_methods[0]
        assert browser_auth_method.id == "browser-auth"
        assert browser_auth_method.name == BROWSER_AUTH_NAME
        assert browser_auth_method.description == BROWSER_AUTH_DESCRIPTION

        auth_method = response.auth_methods[1]
        assert auth_method.id == "vibe-setup"
        assert auth_method.name == "Register your API Key"
        assert auth_method.description == "Register your API Key inside Mistral Vibe"
        assert auth_method.field_meta is not None
        assert "terminal-auth" in auth_method.field_meta
        terminal_auth_meta = auth_method.field_meta["terminal-auth"]
        assert "command" in terminal_auth_meta
        assert "args" in terminal_auth_meta
        assert terminal_auth_meta["args"][-1:] == ["--setup"]
        assert terminal_auth_meta["label"] == "Mistral Vibe Setup"

    @pytest.mark.asyncio
    async def test_initialize_with_delegated_browser_auth(self) -> None:
        acp_agent_loop = build_acp_agent_loop()
        client_capabilities = ClientCapabilities(
            field_meta={"browser-auth-delegated": True}
        )
        response = await acp_agent_loop.initialize(
            protocol_version=PROTOCOL_VERSION, client_capabilities=client_capabilities
        )

        assert response.auth_methods is not None
        assert len(response.auth_methods) == 2

        browser_auth_method = response.auth_methods[0]
        assert browser_auth_method.id == "browser-auth"
        assert browser_auth_method.name == BROWSER_AUTH_NAME
        assert browser_auth_method.description == BROWSER_AUTH_DESCRIPTION

        delegated_browser_auth_method = response.auth_methods[1]
        assert delegated_browser_auth_method.id == "browser-auth-delegated"
        assert delegated_browser_auth_method.name == BROWSER_AUTH_NAME
        assert delegated_browser_auth_method.description == BROWSER_AUTH_DESCRIPTION

    @pytest.mark.asyncio
    async def test_initialize_omits_browser_auth_when_experimental_flag_disabled(
        self,
    ) -> None:
        acp_agent_loop = build_acp_agent_loop(enable_browser_sign_in=False)

        response = await acp_agent_loop.initialize(protocol_version=PROTOCOL_VERSION)

        assert response.auth_methods == []

    @pytest.mark.asyncio
    async def test_initialize_omits_browser_auth_when_provider_unsupported(
        self,
    ) -> None:
        acp_agent_loop = build_acp_agent_loop(
            provider=ProviderConfig(
                name="llamacpp",
                api_base="http://127.0.0.1:8080/v1",
                api_key_env_var="LLAMACPP_API_KEY",
                backend=Backend.GENERIC,
            )
        )

        response = await acp_agent_loop.initialize(protocol_version=PROTOCOL_VERSION)

        assert response.auth_methods == []
