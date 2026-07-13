from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

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

from tests.conftest import build_test_vibe_config
from vibe import __version__
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


def build_acp_agent_loop(*, provider: ProviderConfig | None = None) -> VibeAcpAgentLoop:
    return VibeAcpAgentLoop(
        onboarding_context_loader=lambda: OnboardingContext(
            provider=provider or build_mistral_provider()
        )
    )


@pytest.fixture
def unauthenticated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)


class TestACPInitialize:
    @pytest.mark.asyncio
    async def test_initialize(self, unauthenticated_env: None) -> None:
        acp_agent_loop = build_acp_agent_loop()
        response = await acp_agent_loop.initialize(protocol_version=PROTOCOL_VERSION)

        assert response.protocol_version == PROTOCOL_VERSION
        assert response.agent_capabilities == AgentCapabilities(
            load_session=True,
            prompt_capabilities=PromptCapabilities(
                audio=False, embedded_context=True, image=True
            ),
            session_capabilities=SessionCapabilities(
                close=SessionCloseCapabilities(),
                list=SessionListCapabilities(),
                fork=SessionForkCapabilities(),
            ),
        )
        assert response.agent_info == Implementation(
            name="@mistralai/mistral-vibe", title="Mistral Vibe", version=__version__
        )

        assert response.auth_methods is not None
        assert len(response.auth_methods) == 1
        auth_method = response.auth_methods[0]
        assert auth_method.id == "browser-auth"
        assert auth_method.name == BROWSER_AUTH_NAME
        assert auth_method.description == BROWSER_AUTH_DESCRIPTION

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "reload_method_name", ["_reload_config", "_reload_session_config"]
    )
    async def test_reload_config_preserves_reloaded_config_without_project_name_mutation(
        self, monkeypatch: pytest.MonkeyPatch, reload_method_name: str
    ) -> None:
        config = build_test_vibe_config()
        monkeypatch.setattr(
            "vibe.acp.acp_agent_loop.VibeConfig.load", lambda *args, **kwargs: config
        )
        acp_agent_loop = build_acp_agent_loop()
        mock_config_orchestrator = SimpleNamespace(reload=AsyncMock(), config=config)
        agent_loop = SimpleNamespace(
            config=SimpleNamespace(tool_paths=[]),
            config_orchestrator=mock_config_orchestrator,
            reload_with_initial_messages=AsyncMock(),
        )
        session = cast(Any, SimpleNamespace(agent_loop=agent_loop))

        await acp_agent_loop.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_info=Implementation(name="zed", title="Zed", version="0.999.0"),
        )
        await getattr(acp_agent_loop, reload_method_name)(session)

        mock_config_orchestrator.reload.assert_awaited_once()
        agent_loop.reload_with_initial_messages.assert_awaited_once()
        assert agent_loop.reload_with_initial_messages.await_args.kwargs == {}

    @pytest.mark.asyncio
    async def test_initialize_with_terminal_auth(
        self, unauthenticated_env: None
    ) -> None:
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
                audio=False, embedded_context=True, image=True
            ),
            session_capabilities=SessionCapabilities(
                close=SessionCloseCapabilities(),
                list=SessionListCapabilities(),
                fork=SessionForkCapabilities(),
            ),
        )
        assert response.agent_info == Implementation(
            name="@mistralai/mistral-vibe", title="Mistral Vibe", version=__version__
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
        assert auth_method.args is not None
        assert auth_method.args[-1:] == ["--setup"]
        assert auth_method.field_meta is not None
        assert "terminal-auth" in auth_method.field_meta
        terminal_auth_meta = auth_method.field_meta["terminal-auth"]
        assert "command" in terminal_auth_meta
        assert "args" in terminal_auth_meta
        assert terminal_auth_meta["args"][-1:] == ["--setup"]
        assert terminal_auth_meta["label"] == "Mistral Vibe Setup"

    @pytest.mark.asyncio
    async def test_initialize_with_delegated_browser_auth(
        self, unauthenticated_env: None
    ) -> None:
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

    @pytest.mark.asyncio
    async def test_initialize_omits_auth_methods_for_authenticated_jetbrains_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-api-key")
        acp_agent_loop = build_acp_agent_loop()
        client_capabilities = ClientCapabilities(
            field_meta={"terminal-auth": True, "browser-auth-delegated": True}
        )
        client_info = Implementation(name="JetBrains.PyCharm", version="2026.1.2")

        response = await acp_agent_loop.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=client_capabilities,
            client_info=client_info,
        )

        assert response.auth_methods == []

    @pytest.mark.asyncio
    async def test_initialize_keeps_auth_methods_for_authenticated_non_jetbrains_client(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-api-key")
        acp_agent_loop = build_acp_agent_loop()
        client_capabilities = ClientCapabilities(field_meta={"terminal-auth": True})
        client_info = Implementation(name="zed", version="0.999.0")

        response = await acp_agent_loop.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=client_capabilities,
            client_info=client_info,
        )

        assert response.auth_methods is not None
        assert {method.id for method in response.auth_methods} == {
            "browser-auth",
            "vibe-setup",
        }
