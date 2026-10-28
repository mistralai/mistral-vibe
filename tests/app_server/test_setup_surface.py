"""Session-less ``setup/status`` and ``setup/submit-choices`` wire tests."""

from __future__ import annotations

import json
from pathlib import Path
import tomllib
from typing import Any, Never

import keyring
import pytest
import tomli_w

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
from vibe.core.config.harness_files import HarnessFilesManager
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
async def test_setup_status_carries_the_pinned_console_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The admin pin is normalized to an origin (the trailing slash drops).
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "https://console.acme.internal/")
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.pinned_console_url == "https://console.acme.internal"


@pytest.mark.asyncio
async def test_setup_status_normalizes_a_scheme_less_pinned_console_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A bare domain is upgraded to https:// before it rides the wire.
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "console.acme.internal")
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.pinned_console_url == "https://console.acme.internal"


@pytest.mark.asyncio
async def test_setup_status_pinned_console_url_defaults_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("VIBE_CUSTOM_CONSOLE_URL", raising=False)
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.pinned_console_url is None


@pytest.mark.asyncio
async def test_setup_status_ignores_an_invalid_pinned_console_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "not a url")
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.pinned_console_url is None


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
async def test_setup_submit_choices_pin_wins_over_a_drifted_console_url(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    # A drifted client cannot persist another deployment: the pin is forced
    # over the submitted console URL (and derived onto the provider).
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "https://console.acme.internal")
    client, client_transport, server_transport = await setup_client()
    try:
        status = await _status(client)
        response = validate_wire(
            SetupSubmitChoicesResponse,
            await client.request(
                "setup/submit-choices",
                SetupSubmitChoicesParams(
                    provider=status.provider, console_base_url="https://evil.example"
                ),
            ),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    persisted = tomllib.loads((config_dir / "config.toml").read_text())
    assert persisted["console_base_url"] == "https://console.acme.internal"
    assert persisted["providers"][0]["browser_auth_base_url"] == (
        "https://console.acme.internal"
    )
    assert persisted["providers"][0]["browser_auth_api_base_url"] == (
        "https://console.acme.internal/api"
    )
    # The pin never touches the LLM API base.
    assert persisted["providers"][0]["api_base"] == "https://api.mistral.ai/v1"


@pytest.mark.asyncio
async def test_setup_submit_choices_pin_wins_over_drifted_provider_urls(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    # The provider view's browser-auth URLs are pinned too: they choose where
    # the browser sign-in lands. A scheme-less pin upgrades to https://.
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "console.acme.internal")
    client, client_transport, server_transport = await setup_client()
    try:
        status = await _status(client)
        response = validate_wire(
            SetupSubmitChoicesResponse,
            await client.request(
                "setup/submit-choices",
                SetupSubmitChoicesParams(
                    provider=status.provider.model_copy(
                        update={
                            "browser_auth_base_url": "https://evil.example",
                            "browser_auth_api_base_url": "https://evil.example/api",
                            "browser_auth_allow_origin_rewrite": True,
                        }
                    ),
                    console_base_url="https://evil.example",
                ),
            ),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    persisted = tomllib.loads((config_dir / "config.toml").read_text())
    assert persisted["console_base_url"] == "https://console.acme.internal"
    assert persisted["providers"][0]["browser_auth_base_url"] == (
        "https://console.acme.internal"
    )
    assert persisted["providers"][0]["browser_auth_api_base_url"] == (
        "https://console.acme.internal/api"
    )
    # Same-origin pin: no rewrite, so the default is simply not written.
    assert (
        persisted["providers"][0].get("browser_auth_allow_origin_rewrite") is not True
    )


@pytest.mark.asyncio
async def test_setup_submit_choices_pin_forces_the_console_url_when_absent(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    # Even a client that submits no console URL lands on the pin: the admin
    # pin is the deployment, not a prefill suggestion.
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "https://console.acme.internal")
    client, client_transport, server_transport = await setup_client()
    try:
        response = validate_wire(
            SetupSubmitChoicesResponse,
            await client.request("setup/submit-choices", SetupSubmitChoicesParams()),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    persisted = tomllib.loads((config_dir / "config.toml").read_text())
    assert persisted["console_base_url"] == "https://console.acme.internal"
    # No provider view: the merged seed provider still gets the pin's
    # browser-auth URLs, so the persisted config points at the pin end to end.
    assert persisted["providers"][0]["browser_auth_base_url"] == (
        "https://console.acme.internal"
    )


@pytest.mark.asyncio
async def test_setup_submit_choices_pin_preserves_a_split_horizon_api_base(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    # A split-horizon deployment behind the pin keeps its CLI-reachable
    # connector: the pin fixes the browser sign-in host, not the API base.
    config = get_base_config()
    config["providers"][0].update({
        "browser_auth_base_url": "https://console.acme.internal",
        "browser_auth_api_base_url": "https://connector.acme.internal/api",
        "browser_auth_allow_origin_rewrite": True,
    })
    (config_dir / "config.toml").write_text(tomli_w.dumps(config), encoding="utf-8")
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "https://console.acme.internal")
    client, client_transport, server_transport = await setup_client()
    try:
        status = await _status(client)
        response = validate_wire(
            SetupSubmitChoicesResponse,
            await client.request(
                "setup/submit-choices",
                SetupSubmitChoicesParams(provider=status.provider),
            ),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    persisted = tomllib.loads((config_dir / "config.toml").read_text())
    provider = persisted["providers"][0]
    assert provider["browser_auth_base_url"] == "https://console.acme.internal"
    assert (
        provider["browser_auth_api_base_url"] == "https://connector.acme.internal/api"
    )
    assert provider["browser_auth_allow_origin_rewrite"] is True
    # Account calls keep the CLI-reachable connector origin, not the
    # browser-only console host.
    assert persisted["console_base_url"] == "https://connector.acme.internal"
    # The pin never touches the LLM API base.
    assert provider["api_base"] == "https://api.mistral.ai/v1"


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


_PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"


def _plant_setup_text_plugin(root: Path, name: str, locales: dict[str, str]) -> None:
    plugin_dir = root / ".vibe" / "plugins" / name
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.json").write_text(
        json.dumps({
            "$schema": _PLUGIN_SCHEMA,
            "name": name,
            "extensions": {
                "ai.mistral.vibe": {
                    "schemaVersion": 1,
                    "setupText": {"locales": locales},
                }
            },
        }),
        encoding="utf-8",
    )


async def _setup_client_with_harness_files(
    harness_files: HarnessFilesManager,
) -> tuple[AppServerClient, Any, Any]:
    from vibe.app_server._runtime import HarnessProcess, create_harness_server
    from vibe.app_server.protocol import ClientInfo
    from vibe.app_server.transport import memory_transport_pair

    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(harness_files),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    await client.initialize(ClientInfo(name="setup-test", version="1"))  # type: ignore[arg-type]
    await client.notify("initialized")
    return client, client_transport, server_transport


@pytest.mark.asyncio
async def test_setup_status_carries_the_plugin_setup_text_locales(
    tmp_working_directory: Path,
) -> None:
    from vibe.core.trusted_folders import trusted_folders_manager

    _plant_setup_text_plugin(
        tmp_working_directory,
        "acme",
        {"en": "Welcome to Acme!", "fr": "Bienvenue chez Acme !"},
    )
    trust_store = trusted_folders_manager.for_session()
    trust_store.trust_for_session(tmp_working_directory)
    harness_files = HarnessFilesManager(
        sources=("user", "project"), cwd=tmp_working_directory, trust_store=trust_store
    )
    client, client_transport, server_transport = await _setup_client_with_harness_files(
        harness_files
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.setup_text_locales == {
        "en": "Welcome to Acme!",
        "fr": "Bienvenue chez Acme !",
    }


@pytest.mark.asyncio
async def test_setup_status_setup_text_locales_default_to_none() -> None:
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.setup_text_locales is None
