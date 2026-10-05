"""Session-less ``setup/status`` and ``setup/submit-choices`` wire tests."""

from __future__ import annotations

from pathlib import Path
import tomllib
from typing import Any, Never

import keyring
import pytest

from tests.app_server._helpers import setup_client
from tests.conftest import get_base_config
from vibe.app_server._model import validate_wire
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    SERVER_METHODS,
    AppServerResponseError,
    ProtocolErrorCode,
    SetupProviderView,
    SetupStatusParams,
    SetupStatusResponse,
    SetupSubmitChoicesParams,
    SetupSubmitChoicesResponse,
)
from vibe.core.config import (
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_THEME,
    DEFAULT_VIBE_BASE_URL,
)
from vibe.setup.auth.api_key_persistence import ProviderCredentialsPersistResult
from vibe.utils.keyring import clear_api_key_keyring_cache


async def _status(client: AppServerClient) -> SetupStatusResponse:
    return validate_wire(
        SetupStatusResponse, await client.request("setup/status", SetupStatusParams())
    )


@pytest.mark.asyncio
async def test_setup_status_seeds_the_wizard_from_the_resolved_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.provider.name == "mistral"
    assert response.provider.api_base == "https://api.mistral.ai/v1"
    assert response.provider.api_key_env_var == "MISTRAL_API_KEY"
    assert response.provider.browser_auth_base_url == "https://console.mistral.ai"
    assert (
        response.provider.browser_auth_api_base_url == "https://console.mistral.ai/api"
    )
    assert response.provider.browser_auth_allow_origin_rewrite is False
    assert response.console_base_url == DEFAULT_CONSOLE_BASE_URL
    assert response.vibe_base_url == DEFAULT_VIBE_BASE_URL
    assert response.active_model == "devstral-latest"
    assert response.theme == DEFAULT_THEME
    assert response.supports_browser_sign_in is True
    assert response.has_api_key is False


@pytest.mark.asyncio
async def test_setup_status_has_api_key_true_from_process_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "env-key")

    def _fail(service: str, username: str) -> str | None:
        raise AssertionError("keyring must not be consulted when env is set")

    monkeypatch.setattr(keyring, "get_password", _fail)
    clear_api_key_keyring_cache()
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.has_api_key is True


@pytest.mark.asyncio
async def test_setup_status_has_api_key_true_from_keyring_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "keyring-key"
    )
    clear_api_key_keyring_cache()
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.has_api_key is True


@pytest.mark.asyncio
async def test_setup_submit_choices_writes_nothing_without_drift(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    async def _unexpected(*args: Any, **kwargs: Any) -> Never:
        raise AssertionError("no drift must not write config")

    monkeypatch.setattr(
        "vibe.app_server._setup.persist_provider_credentials", _unexpected
    )
    client, client_transport, server_transport = await setup_client()
    try:
        status = await _status(client)
        response = validate_wire(
            SetupSubmitChoicesResponse,
            await client.request(
                "setup/submit-choices",
                SetupSubmitChoicesParams(
                    provider=status.provider,
                    console_base_url=status.console_base_url,
                    vibe_base_url=status.vibe_base_url,
                ),
            ),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    assert response.failures == []
    assert "theme" not in tomllib.loads((config_dir / "config.toml").read_text())


@pytest.mark.asyncio
async def test_setup_submit_choices_persists_only_drifted_fields(
    config_dir: Path,
) -> None:
    client, client_transport, server_transport = await setup_client()
    try:
        status = await _status(client)
        response = validate_wire(
            SetupSubmitChoicesResponse,
            await client.request(
                "setup/submit-choices",
                SetupSubmitChoicesParams(
                    provider=status.provider.model_copy(
                        update={"browser_auth_base_url": "https://custom.example"}
                    ),
                    console_base_url="https://custom.example",
                    theme=status.theme,
                ),
            ),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    persisted = tomllib.loads((config_dir / "config.toml").read_text())
    # Keyed upsert by name: the provider entry is replaced, not appended.
    assert [provider["name"] for provider in persisted["providers"]] == ["mistral"]
    assert persisted["providers"][0]["browser_auth_base_url"] == (
        "https://custom.example"
    )
    assert persisted["console_base_url"] == "https://custom.example"
    assert persisted["theme"] == status.theme
    # No vibe_base_url drift, so the field is never pinned into config.toml.
    assert "vibe_base_url" not in persisted


@pytest.mark.asyncio
async def test_setup_submit_choices_unknown_provider_persists_fresh_view(
    config_dir: Path,
) -> None:
    """A provider name absent from config starts a fresh provider merged from
    the six wizard-visible fields; exclude_defaults pins nothing else.
    """
    client, client_transport, server_transport = await setup_client()
    try:
        response = validate_wire(
            SetupSubmitChoicesResponse,
            await client.request(
                "setup/submit-choices",
                SetupSubmitChoicesParams(
                    provider=SetupProviderView(
                        name="acme",
                        api_base="https://acme.example/v1",
                        api_key_env_var="ACME_API_KEY",
                        browser_auth_base_url="https://console.acme.example",
                    )
                ),
            ),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    persisted = tomllib.loads((config_dir / "config.toml").read_text())
    providers = {provider["name"]: provider for provider in persisted["providers"]}
    # The fresh provider carries exactly the wizard's view: the untouched
    # settings (backend, api_style, ...) stay out of the payload entirely.
    assert providers["acme"] == {
        "name": "acme",
        "api_base": "https://acme.example/v1",
        "api_key_env_var": "ACME_API_KEY",
        "browser_auth_base_url": "https://console.acme.example",
    }


@pytest.mark.asyncio
async def test_setup_submit_choices_reports_each_failed_field(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    async def _failing(request: Any, *, reason: str = "onboarding") -> Any:
        return ProviderCredentialsPersistResult(
            provider=True, console_base_url=False, vibe_base_url=False
        )

    monkeypatch.setattr("vibe.app_server._setup.persist_provider_credentials", _failing)
    client, client_transport, server_transport = await setup_client()
    try:
        response = validate_wire(
            SetupSubmitChoicesResponse,
            await client.request(
                "setup/submit-choices",
                SetupSubmitChoicesParams(console_base_url="https://custom.example"),
            ),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "provider_config_error"
    assert response.failures == ["console_base_url", "vibe_base_url"]


@pytest.mark.asyncio
async def test_setup_status_seeds_the_named_provider_not_the_active_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The failed session's handshake names the provider on ``setup/status``.

    The wizard must seed for the provider that actually lacks a key (an
    ``--agent`` run's config layering can differ from the active provider),
    resolved through the runtime's own ``_named_provider`` path.
    """
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    client, client_transport, server_transport = await setup_client()
    try:
        response = validate_wire(
            SetupStatusResponse,
            await client.request(
                "setup/status", SetupStatusParams(provider="llamacpp")
            ),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.provider.name == "llamacpp"
    assert response.provider.api_base == "http://127.0.0.1:8080/v1"
    assert response.supports_browser_sign_in is False


@pytest.mark.asyncio
async def test_setup_status_rejects_an_unknown_named_provider() -> None:
    client, client_transport, server_transport = await setup_client()
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request(
                "setup/status", SetupStatusParams(provider="no-such-provider")
            )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS


@pytest.mark.asyncio
async def test_server_without_setup_methods_answers_method_not_found(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    """The old-server half of the ``setup/*`` version skew (ADR 0014).

    ``setup/*`` is additive: an old server never advertised the methods, so a
    newer client's request dies as a typed method-not-found error and nothing
    is written. The client surfaces that as setup-unavailable; it never falls
    back to writing credentials locally.
    """
    monkeypatch.setattr(
        "vibe.app_server.server.SERVER_METHODS",
        tuple(method for method in SERVER_METHODS if not method.startswith("setup/")),
    )
    client, client_transport, server_transport = await setup_client()
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request("setup/status", SetupStatusParams())
    finally:
        await client_transport.close()
        await server_transport.close()

    assert exc_info.value.error.code is ProtocolErrorCode.METHOD_NOT_FOUND
    assert tomllib.loads((config_dir / "config.toml").read_text()) == get_base_config()
