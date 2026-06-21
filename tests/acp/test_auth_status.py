from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values
import keyring
from keyring.errors import KeyringError
import pytest

from vibe.acp.acp_agent_loop import VibeAcpAgentLoop
from vibe.acp.exceptions import InternalError, InvalidRequestError
from vibe.core.config import (
    DEFAULT_MISTRAL_API_ENV_KEY,
    ProviderConfig,
    load_dotenv_values,
)
from vibe.core.types import Backend
from vibe.setup.auth import AuthStateKind
from vibe.setup.onboarding.context import OnboardingContext


@pytest.fixture(autouse=True)
def disable_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyring, "get_password", lambda service, username: None)
    monkeypatch.setattr(
        keyring, "set_password", lambda service, username, password: None
    )
    monkeypatch.setattr(keyring, "delete_password", lambda service, username: None)


def build_mistral_provider(
    *, api_key_env_var: str = DEFAULT_MISTRAL_API_ENV_KEY
) -> ProviderConfig:
    return ProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai/v1",
        api_key_env_var=api_key_env_var,
        browser_auth_base_url="https://console.mistral.ai",
        browser_auth_api_base_url="https://console.mistral.ai/api",
        backend=Backend.MISTRAL,
    )


def build_generic_provider(
    *, name: str = "custom", api_key_env_var: str = "CUSTOM_API_KEY"
) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        api_base="https://custom.example/v1",
        api_key_env_var=api_key_env_var,
        backend=Backend.GENERIC,
    )


def build_acp_agent_loop(
    provider: ProviderConfig,
    *,
    environ_before_dotenv_load: dict[str, str] | None = None,
) -> VibeAcpAgentLoop:
    return VibeAcpAgentLoop(
        onboarding_context_loader=lambda: OnboardingContext(provider=provider),
        environ_before_dotenv_load=environ_before_dotenv_load,
    )


def write_env_file(config_dir: Path, content: str) -> None:
    (config_dir / ".env").write_text(content, encoding="utf-8")


class TestACPAuthStatus:
    @pytest.mark.asyncio
    async def test_returns_signed_out_when_no_key_source(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DEFAULT_MISTRAL_API_ENV_KEY, raising=False)
        acp_agent_loop = build_acp_agent_loop(build_mistral_provider())

        response = await acp_agent_loop.ext_method("auth/status", {})

        assert response == {
            "authenticated": False,
            "authState": AuthStateKind.SIGNED_OUT.value,
            "signOutAvailable": False,
        }

    @pytest.mark.asyncio
    async def test_returns_sign_out_available_for_default_dotenv_key(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DEFAULT_MISTRAL_API_ENV_KEY, raising=False)
        write_env_file(config_dir, f"{DEFAULT_MISTRAL_API_ENV_KEY}=file-key\n")
        acp_agent_loop = build_acp_agent_loop(build_mistral_provider())

        response = await acp_agent_loop.ext_method("auth/status", {})

        assert response == {
            "authenticated": True,
            "authState": AuthStateKind.VIBE_HOME_ENV_FILE.value,
            "signOutAvailable": True,
        }

    @pytest.mark.asyncio
    async def test_uses_startup_env_snapshot_for_dotenv_key(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DEFAULT_MISTRAL_API_ENV_KEY, raising=False)
        write_env_file(config_dir, f"{DEFAULT_MISTRAL_API_ENV_KEY}=file-key\n")
        environ_before_dotenv_load = os.environ.copy()
        load_dotenv_values()
        acp_agent_loop = build_acp_agent_loop(
            build_mistral_provider(),
            environ_before_dotenv_load=environ_before_dotenv_load,
        )

        await acp_agent_loop.ext_method("auth/status", {})
        response = await acp_agent_loop.ext_method("auth/status", {})

        assert response == {
            "authenticated": True,
            "authState": AuthStateKind.VIBE_HOME_ENV_FILE.value,
            "signOutAvailable": True,
        }

    @pytest.mark.asyncio
    async def test_returns_process_env_when_key_only_exists_before_dotenv(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(DEFAULT_MISTRAL_API_ENV_KEY, "process-key")
        acp_agent_loop = build_acp_agent_loop(build_mistral_provider())

        response = await acp_agent_loop.ext_method("auth/status", {})

        assert response == {
            "authenticated": True,
            "authState": AuthStateKind.PROCESS_ENV.value,
            "signOutAvailable": False,
        }

    @pytest.mark.asyncio
    async def test_returns_process_env_when_process_env_existed_before_dotenv(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(DEFAULT_MISTRAL_API_ENV_KEY, "process-key")
        write_env_file(config_dir, f"{DEFAULT_MISTRAL_API_ENV_KEY}=file-key\n")
        acp_agent_loop = build_acp_agent_loop(build_mistral_provider())

        response = await acp_agent_loop.ext_method("auth/status", {})

        assert response == {
            "authenticated": True,
            "authState": AuthStateKind.PROCESS_ENV.value,
            "signOutAvailable": False,
        }

    @pytest.mark.asyncio
    async def test_returns_keyring_when_key_only_exists_in_keyring(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DEFAULT_MISTRAL_API_ENV_KEY, raising=False)
        monkeypatch.setattr(
            keyring, "get_password", lambda service, username: "keyring-key"
        )
        acp_agent_loop = build_acp_agent_loop(build_mistral_provider())

        response = await acp_agent_loop.ext_method("auth/status", {})

        assert response == {
            "authenticated": True,
            "authState": AuthStateKind.OS_KEYRING.value,
            "signOutAvailable": True,
        }

    @pytest.mark.asyncio
    async def test_returns_auth_not_required_for_provider_without_env_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DEFAULT_MISTRAL_API_ENV_KEY, raising=False)
        acp_agent_loop = build_acp_agent_loop(
            build_generic_provider(name="llamacpp", api_key_env_var="")
        )

        response = await acp_agent_loop.ext_method("auth/status", {})

        assert response == {
            "authenticated": True,
            "authState": AuthStateKind.AUTH_NOT_REQUIRED.value,
            "signOutAvailable": False,
        }

    @pytest.mark.asyncio
    async def test_returns_unsupported_provider_for_custom_key_setup(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
        write_env_file(config_dir, "CUSTOM_API_KEY=file-key\n")
        acp_agent_loop = build_acp_agent_loop(build_generic_provider())

        response = await acp_agent_loop.ext_method("auth/status", {})

        assert response == {
            "authenticated": True,
            "authState": AuthStateKind.UNSUPPORTED_PROVIDER.value,
            "signOutAvailable": False,
        }


class TestACPAuthSignOut:
    @pytest.mark.asyncio
    async def test_removes_default_dotenv_key(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DEFAULT_MISTRAL_API_ENV_KEY, raising=False)
        write_env_file(
            config_dir, f"{DEFAULT_MISTRAL_API_ENV_KEY}=file-key\nOTHER_KEY=other\n"
        )
        acp_agent_loop = build_acp_agent_loop(build_mistral_provider())

        response = await acp_agent_loop.ext_method("auth/signOut", {})

        env_values = dotenv_values(config_dir / ".env")
        assert response == {}
        assert DEFAULT_MISTRAL_API_ENV_KEY not in env_values
        assert env_values["OTHER_KEY"] == "other"
        assert DEFAULT_MISTRAL_API_ENV_KEY not in os.environ
        assert await acp_agent_loop.ext_method("auth/status", {}) == {
            "authenticated": False,
            "authState": AuthStateKind.SIGNED_OUT.value,
            "signOutAvailable": False,
        }

    @pytest.mark.asyncio
    async def test_refuses_process_env_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(DEFAULT_MISTRAL_API_ENV_KEY, "process-key")
        acp_agent_loop = build_acp_agent_loop(build_mistral_provider())

        with pytest.raises(InvalidRequestError, match=AuthStateKind.PROCESS_ENV.value):
            await acp_agent_loop.ext_method("auth/signOut", {})

        assert os.environ[DEFAULT_MISTRAL_API_ENV_KEY] == "process-key"

    @pytest.mark.asyncio
    async def test_refuses_dotenv_key_when_process_env_key_exists(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(DEFAULT_MISTRAL_API_ENV_KEY, "process-key")
        write_env_file(config_dir, f"{DEFAULT_MISTRAL_API_ENV_KEY}=file-key\n")
        acp_agent_loop = build_acp_agent_loop(build_mistral_provider())

        with pytest.raises(InvalidRequestError, match=AuthStateKind.PROCESS_ENV.value):
            await acp_agent_loop.ext_method("auth/signOut", {})

        assert (
            dotenv_values(config_dir / ".env")[DEFAULT_MISTRAL_API_ENV_KEY]
            == "file-key"
        )
        assert os.environ[DEFAULT_MISTRAL_API_ENV_KEY] == "process-key"

    @pytest.mark.asyncio
    async def test_removes_keyring_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        deleted: list[str] = []
        monkeypatch.delenv(DEFAULT_MISTRAL_API_ENV_KEY, raising=False)
        monkeypatch.setattr(
            keyring, "get_password", lambda service, username: "keyring-key"
        )
        monkeypatch.setattr(
            keyring,
            "delete_password",
            lambda service, username: deleted.append(username),
        )
        acp_agent_loop = build_acp_agent_loop(build_mistral_provider())

        response = await acp_agent_loop.ext_method("auth/signOut", {})

        assert response == {}
        assert deleted == [DEFAULT_MISTRAL_API_ENV_KEY]

    @pytest.mark.asyncio
    async def test_surfaces_internal_error_when_keyring_delete_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DEFAULT_MISTRAL_API_ENV_KEY, raising=False)
        monkeypatch.setattr(
            keyring, "get_password", lambda service, username: "keyring-key"
        )

        def _failed(service: str, username: str) -> None:
            raise KeyringError("delete failed")

        monkeypatch.setattr(keyring, "delete_password", _failed)
        acp_agent_loop = build_acp_agent_loop(build_mistral_provider())

        with pytest.raises(InternalError, match="Failed to sign out"):
            await acp_agent_loop.ext_method("auth/signOut", {})

    @pytest.mark.asyncio
    async def test_refuses_unsupported_provider_key(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CUSTOM_API_KEY", raising=False)
        write_env_file(config_dir, "CUSTOM_API_KEY=file-key\n")
        acp_agent_loop = build_acp_agent_loop(build_generic_provider())

        with pytest.raises(
            InvalidRequestError, match=AuthStateKind.UNSUPPORTED_PROVIDER.value
        ):
            await acp_agent_loop.ext_method("auth/signOut", {})

        assert dotenv_values(config_dir / ".env")["CUSTOM_API_KEY"] == "file-key"

    @pytest.mark.asyncio
    async def test_refuses_auth_not_required(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DEFAULT_MISTRAL_API_ENV_KEY, raising=False)
        acp_agent_loop = build_acp_agent_loop(
            build_generic_provider(name="llamacpp", api_key_env_var="")
        )

        with pytest.raises(
            InvalidRequestError, match=AuthStateKind.AUTH_NOT_REQUIRED.value
        ):
            await acp_agent_loop.ext_method("auth/signOut", {})

    @pytest.mark.asyncio
    async def test_refuses_signed_out_state(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(DEFAULT_MISTRAL_API_ENV_KEY, raising=False)
        acp_agent_loop = build_acp_agent_loop(build_mistral_provider())

        with pytest.raises(InvalidRequestError, match=AuthStateKind.SIGNED_OUT.value):
            await acp_agent_loop.ext_method("auth/signOut", {})
