"""Session-less ``setup/status`` and ``setup/submit-choices`` wire tests."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
import tomllib
from typing import Any, Never

import keyring
import pytest
import tomli_w

from tests.app_server._helpers import setup_client
from tests.conftest import get_base_config
from vibe.app_server._model import validate_wire
from vibe.app_server._process_services import ProcessServices
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
from vibe.app_server.provider_auth import ProviderCredentialSourceKind
from vibe.core.config import (
    DEFAULT_CONSOLE_BASE_URL,
    DEFAULT_THEME,
    DEFAULT_VIBE_BASE_URL,
    load_dotenv_values,
)
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.setup.auth.api_key_persistence import ProviderCredentialsPersistResult
from vibe.utils.keyring import clear_api_key_keyring_cache


async def _status(client: AppServerClient) -> SetupStatusResponse:
    return validate_wire(
        SetupStatusResponse, await client.request("setup/status", SetupStatusParams())
    )


@pytest.mark.asyncio
async def test_setup_status_seeds_the_wizard_from_the_resolved_config(
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
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
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "env-key")

    def _fail(service: str, username: str) -> str | None:
        raise AssertionError("keyring must not be consulted when env is set")

    monkeypatch.setattr(keyring, "get_password", _fail)
    clear_api_key_keyring_cache()
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.has_api_key is True


@pytest.mark.asyncio
async def test_setup_status_has_api_key_true_from_keyring_only(
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    monkeypatch.setattr(
        keyring, "get_password", lambda service, username: "keyring-key"
    )
    clear_api_key_keyring_cache()
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.has_api_key is True


@pytest.fixture
def restore_env_value_origins() -> Iterator[None]:
    import vibe.core.config.vibe_schema as vibe_schema

    snapshot = dict(vibe_schema._ENV_VALUE_ORIGINS)
    yield
    vibe_schema._ENV_VALUE_ORIGINS.clear()
    vibe_schema._ENV_VALUE_ORIGINS.update(snapshot)


@pytest.mark.asyncio
@pytest.mark.usefixtures("restore_env_value_origins")
async def test_setup_status_reports_a_shell_key_over_a_different_env_file_key(
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """The shell key wins over ``.env``: a key setup saves would be shadowed."""
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_ENV_FILE.path.write_text("MISTRAL_API_KEY=file-key\n")
    monkeypatch.setenv("MISTRAL_API_KEY", "shell-key")
    load_dotenv_values(GLOBAL_ENV_FILE.path)

    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.active_credential_source is ProviderCredentialSourceKind.PROCESS_ENV


@pytest.mark.asyncio
@pytest.mark.usefixtures("restore_env_value_origins")
async def test_setup_status_reports_a_key_loaded_from_the_env_file(
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_ENV_FILE.path.write_text("MISTRAL_API_KEY=file-key\n")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    load_dotenv_values(GLOBAL_ENV_FILE.path)

    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()
        monkeypatch.delenv("MISTRAL_API_KEY", raising=False)

    assert (
        response.active_credential_source
        is ProviderCredentialSourceKind.VIBE_HOME_ENV_FILE
    )


@pytest.mark.asyncio
async def test_setup_status_carries_the_pinned_console_url(
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    # The admin pin is normalized to an origin (the trailing slash drops).
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "https://console.acme.internal/")
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.pinned_console_url == "https://console.acme.internal"


@pytest.mark.asyncio
async def test_setup_status_normalizes_a_scheme_less_pinned_console_url(
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    # A bare domain is upgraded to https:// before it rides the wire.
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "console.acme.internal")
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.pinned_console_url == "https://console.acme.internal"


@pytest.mark.asyncio
async def test_setup_status_pinned_console_url_defaults_to_none(
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    monkeypatch.delenv("VIBE_CUSTOM_CONSOLE_URL", raising=False)
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.pinned_console_url is None


@pytest.mark.asyncio
async def test_setup_status_ignores_an_invalid_pinned_console_url(
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "not a url")
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.pinned_console_url is None


@pytest.mark.asyncio
async def test_setup_submit_choices_writes_nothing_without_drift(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, process_services: ProcessServices
) -> None:
    async def _unexpected(*args: Any, **kwargs: Any) -> Never:
        raise AssertionError("no drift must not write config")

    monkeypatch.setattr(
        "vibe.app_server._setup.persist_provider_credentials", _unexpected
    )
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
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
    config_dir: Path, process_services: ProcessServices
) -> None:
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
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
    config_dir: Path, process_services: ProcessServices
) -> None:
    """A provider name absent from config starts a fresh provider merged from
    the six wizard-visible fields; exclude_defaults pins nothing else.
    """
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
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
async def test_setup_submit_choices_preserves_pinned_api_base(
    config_dir: Path, process_services: ProcessServices
) -> None:
    """A provider with preserve_api_base = true keeps its configured api_base
    even when the wizard view carries the whoami-overwritten one (the thin
    client resolves tenant domains before submitting).
    """
    config_file = config_dir / "config.toml"
    base = tomllib.loads(config_file.read_text(encoding="utf-8"))
    base["providers"][0]["api_base"] = "https://connector.internal.example:443/v1"
    base["providers"][0]["preserve_api_base"] = True
    config_file.write_text(tomli_w.dumps(base), encoding="utf-8")

    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        status = await _status(client)
        response = validate_wire(
            SetupSubmitChoicesResponse,
            await client.request(
                "setup/submit-choices",
                SetupSubmitChoicesParams(
                    # The client's whoami resolution overwrote api_base with
                    # the tenant's public host; the flag must veto it.
                    provider=status.provider.model_copy(
                        update={"api_base": "https://api.tenant.example/v1"}
                    ),
                    vibe_base_url="https://chat.tenant.example",
                ),
            ),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    persisted = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert persisted["providers"][0]["api_base"] == (
        "https://connector.internal.example:443/v1"
    )
    assert persisted["providers"][0]["preserve_api_base"] is True
    assert persisted["vibe_base_url"] == "https://chat.tenant.example"


@pytest.mark.asyncio
async def test_setup_submit_choices_preserves_pinned_api_base_with_other_drift(
    config_dir: Path, process_services: ProcessServices
) -> None:
    """With preserve_api_base = true and another view field drifting, the
    provider persist still runs, keeps the pinned api_base, and round-trips
    the flag itself.
    """
    config_file = config_dir / "config.toml"
    base = tomllib.loads(config_file.read_text(encoding="utf-8"))
    base["providers"][0]["api_base"] = "https://connector.internal.example:443/v1"
    base["providers"][0]["preserve_api_base"] = True
    config_file.write_text(tomli_w.dumps(base), encoding="utf-8")

    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        status = await _status(client)
        response = validate_wire(
            SetupSubmitChoicesResponse,
            await client.request(
                "setup/submit-choices",
                SetupSubmitChoicesParams(
                    provider=status.provider.model_copy(
                        update={
                            "api_base": "https://api.tenant.example/v1",
                            "browser_auth_base_url": "https://console.internal.example",
                        }
                    )
                ),
            ),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    persisted = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert persisted["providers"][0]["api_base"] == (
        "https://connector.internal.example:443/v1"
    )
    assert persisted["providers"][0]["preserve_api_base"] is True
    assert persisted["providers"][0]["browser_auth_base_url"] == (
        "https://console.internal.example"
    )


@pytest.mark.asyncio
async def test_setup_submit_choices_preserves_pinned_api_base_when_view_is_blank(
    config_dir: Path, process_services: ProcessServices
) -> None:
    """A blank view api_base must neither crash the merge nor un-pin the
    configured value: the veto drops the key whatever the wizard sent.
    """
    config_file = config_dir / "config.toml"
    base = tomllib.loads(config_file.read_text(encoding="utf-8"))
    base["providers"][0]["api_base"] = "https://connector.internal.example:443/v1"
    base["providers"][0]["preserve_api_base"] = True
    config_file.write_text(tomli_w.dumps(base), encoding="utf-8")

    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        status = await _status(client)
        response = validate_wire(
            SetupSubmitChoicesResponse,
            await client.request(
                "setup/submit-choices",
                SetupSubmitChoicesParams(
                    provider=status.provider.model_copy(update={"api_base": ""}),
                    vibe_base_url="https://chat.tenant.example",
                ),
            ),
        )
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    persisted = tomllib.loads(config_file.read_text(encoding="utf-8"))
    assert persisted["providers"][0]["api_base"] == (
        "https://connector.internal.example:443/v1"
    )
    assert persisted["vibe_base_url"] == "https://chat.tenant.example"


@pytest.mark.asyncio
async def test_setup_submit_choices_reports_each_failed_field(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, process_services: ProcessServices
) -> None:
    async def _failing(request: Any, *, reason: str = "onboarding") -> Any:
        return ProviderCredentialsPersistResult(
            provider=True, console_base_url=False, vibe_base_url=False
        )

    monkeypatch.setattr("vibe.app_server._setup.persist_provider_credentials", _failing)
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
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
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, process_services: ProcessServices
) -> None:
    # A drifted client cannot persist another deployment: the pin is forced
    # over the submitted console URL (and derived onto the provider).
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "https://console.acme.internal")
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
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
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, process_services: ProcessServices
) -> None:
    # The provider view's browser-auth URLs are pinned too: they choose where
    # the browser sign-in lands. A scheme-less pin upgrades to https://.
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "console.acme.internal")
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
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
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, process_services: ProcessServices
) -> None:
    # Even a client that submits no console URL lands on the pin: the admin
    # pin is the deployment, not a prefill suggestion.
    monkeypatch.setenv("VIBE_CUSTOM_CONSOLE_URL", "https://console.acme.internal")
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
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
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, process_services: ProcessServices
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
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
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
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """The failed session's handshake names the provider on ``setup/status``.

    The wizard must seed for the provider that actually lacks a key (an
    ``--agent`` run's config layering can differ from the active provider),
    resolved through the runtime's own ``_named_provider`` path.
    """
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
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
async def test_setup_status_offers_mistral_next_to_a_non_mistral_active_provider(
    config_dir: Path, process_services: ProcessServices
) -> None:
    config = get_base_config()
    config["providers"].append({
        "name": "litellm",
        "api_base": "http://localhost:4000/v1",
        "api_key_env_var": "LITELLM_API_KEY",
    })
    config["models"].append({
        "name": "gpt-5",
        "provider": "litellm",
        "alias": "litellm-gpt",
    })
    config["active_model"] = "litellm-gpt"
    (config_dir / "config.toml").write_text(tomli_w.dumps(config), encoding="utf-8")
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.provider.name == "litellm"
    assert response.supports_browser_sign_in is False
    assert response.mistral_provider is not None
    assert response.mistral_provider.name == "mistral"
    assert response.mistral_provider.browser_auth_base_url == (
        "https://console.mistral.ai"
    )


@pytest.mark.asyncio
@pytest.mark.usefixtures("restore_env_value_origins")
async def test_setup_status_reports_the_offered_mistral_provider_shell_key(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices
) -> None:
    """The offered Mistral provider's own shell key, not the seeded one's."""
    config = get_base_config()
    config["providers"].append({
        "name": "litellm",
        "api_base": "http://localhost:4000/v1",
        "api_key_env_var": "LITELLM_API_KEY",
    })
    config["models"].append({
        "name": "gpt-5",
        "provider": "litellm",
        "alias": "litellm-gpt",
    })
    config["active_model"] = "litellm-gpt"
    (config_dir / "config.toml").write_text(tomli_w.dumps(config), encoding="utf-8")
    monkeypatch.delenv("LITELLM_API_KEY", raising=False)
    monkeypatch.setenv("MISTRAL_API_KEY", "shell-key")
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.active_credential_source is None
    assert (
        response.mistral_active_credential_source
        is ProviderCredentialSourceKind.PROCESS_ENV
    )


@pytest.mark.asyncio
async def test_setup_status_offers_no_extra_provider_for_mistral(
    process_services: ProcessServices,
) -> None:
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        response = await _status(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.mistral_provider is None


@pytest.mark.asyncio
async def test_setup_status_rejects_an_unknown_named_provider(
    process_services: ProcessServices,
) -> None:
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
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
    monkeypatch: pytest.MonkeyPatch, config_dir: Path, process_services: ProcessServices
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
    client, client_transport, server_transport = await setup_client(
        process_services=process_services
    )
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await client.request("setup/status", SetupStatusParams())
    finally:
        await client_transport.close()
        await server_transport.close()

    assert exc_info.value.error.code is ProtocolErrorCode.METHOD_NOT_FOUND
    assert tomllib.loads((config_dir / "config.toml").read_text()) == get_base_config()
