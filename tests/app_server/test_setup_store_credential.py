"""Session-less ``setup/store-credential`` wire tests."""

from __future__ import annotations

import os
from pathlib import Path
import stat
from typing import Any

from dotenv import dotenv_values
import keyring
from keyring.errors import KeyringError
import pytest
import tomli_w

from tests.app_server._helpers import setup_client
from tests.conftest import get_base_config
from vibe.app_server._model import validate_wire
from vibe.app_server.client import AppServerClient
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolErrorCode,
    SetupStoreCredentialParams,
    SetupStoreCredentialResponse,
    SetupSubmitChoicesParams,
)
from vibe.core.paths import GLOBAL_ENV_FILE
from vibe.setup.auth import api_key_persistence
from vibe.utils.keyring import clear_api_key_keyring_cache


async def _store(
    client: AppServerClient,
    *,
    provider: str = "mistral",
    api_key: str = "new-key",
    custom_domain: bool = False,
) -> SetupStoreCredentialResponse:
    return validate_wire(
        SetupStoreCredentialResponse,
        await client.request(
            "setup/store-credential",
            SetupStoreCredentialParams(
                provider=provider, api_key=api_key, custom_domain=custom_domain
            ),
        ),
    )


def _write_custom_provider_config(config_file: Path, *, api_key_env_var: str) -> None:
    base = get_base_config()
    base["providers"].append({
        "name": "custom",
        "api_base": "https://custom.example/v1",
        "api_key_env_var": api_key_env_var,
    })
    base["models"].append({
        "name": "custom-model",
        "provider": "custom",
        "alias": "custom-model",
    })
    base["active_model"] = "custom-model"
    config_file.write_text(tomli_w.dumps(base), encoding="utf-8")


@pytest.mark.asyncio
async def test_store_credential_completed_writes_keyring_and_process_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "placeholder")
    written: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        keyring, "set_password", lambda s, u, p: written.append((s, u, p))
    )
    clear_api_key_keyring_cache()
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _store(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    assert response.detail is None
    assert os.environ["MISTRAL_API_KEY"] == "new-key"
    assert written == [("ai.mistral.vibe", "MISTRAL_API_KEY", "new-key")]
    # The keyring write succeeded, so no plaintext .env fallback is created.
    assert not GLOBAL_ENV_FILE.path.exists()


@pytest.mark.asyncio
async def test_store_credential_env_var_error_for_invalid_env_var_name(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    _write_custom_provider_config(
        config_dir / "config.toml", api_key_env_var="BAD=NAME"
    )
    monkeypatch.setenv("MISTRAL_API_KEY", "placeholder")

    def _fail(service: str, username: str, password: str) -> None:
        raise AssertionError("keyring must not be used on env_var_error")

    monkeypatch.setattr(keyring, "set_password", _fail)
    clear_api_key_keyring_cache()
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _store(client, provider="custom")
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "env_var_error"
    assert response.detail == "BAD=NAME"
    assert not GLOBAL_ENV_FILE.path.exists()


@pytest.mark.asyncio
async def test_store_credential_empty_env_var_falls_back_to_mistral_var(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    # Python parity (resolve_api_key_provider): the default Mistral env var.
    _write_custom_provider_config(config_dir / "config.toml", api_key_env_var="")
    monkeypatch.setenv("MISTRAL_API_KEY", "placeholder")
    written: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        keyring, "set_password", lambda s, u, p: written.append((s, u, p))
    )
    clear_api_key_keyring_cache()
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _store(client, provider="custom")
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    assert written == [("ai.mistral.vibe", "MISTRAL_API_KEY", "new-key")]


@pytest.mark.asyncio
async def test_store_credential_save_error_when_keyring_and_env_file_both_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "placeholder")

    def _unavailable(service: str, username: str, password: str) -> None:
        raise KeyringError("no keyring")

    monkeypatch.setattr(keyring, "set_password", _unavailable)
    clear_api_key_keyring_cache()
    # A directory where the .env file belongs makes the write fail itself.
    GLOBAL_ENV_FILE.path.mkdir(parents=True, exist_ok=True)
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _store(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "save_error"
    assert response.detail is not None
    # The key is still exported to the server process env, exactly like Python.
    assert os.environ["MISTRAL_API_KEY"] == "new-key"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
async def test_store_credential_env_file_fallback_is_owner_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "placeholder")

    def _unavailable(service: str, username: str, password: str) -> None:
        raise KeyringError("no keyring")

    monkeypatch.setattr(keyring, "set_password", _unavailable)
    clear_api_key_keyring_cache()
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _store(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    assert stat.S_IMODE(os.lstat(GLOBAL_ENV_FILE.path).st_mode) == 0o600
    assert dotenv_values(GLOBAL_ENV_FILE.path)["MISTRAL_API_KEY"] == "new-key"


@pytest.mark.asyncio
@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
async def test_store_credential_tightens_existing_env_file_to_owner_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "placeholder")

    def _unavailable(service: str, username: str, password: str) -> None:
        raise KeyringError("no keyring")

    monkeypatch.setattr(keyring, "set_password", _unavailable)
    clear_api_key_keyring_cache()
    GLOBAL_ENV_FILE.path.parent.mkdir(parents=True, exist_ok=True)
    GLOBAL_ENV_FILE.path.write_text("MISTRAL_API_KEY=stale\n", encoding="utf-8")
    os.chmod(GLOBAL_ENV_FILE.path, 0o644)
    client, client_transport, server_transport = await setup_client()
    try:
        response = await _store(client)
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    assert stat.S_IMODE(os.lstat(GLOBAL_ENV_FILE.path).st_mode) == 0o600
    assert dotenv_values(GLOBAL_ENV_FILE.path)["MISTRAL_API_KEY"] == "new-key"


@pytest.mark.asyncio
async def test_store_credential_unknown_provider_is_rejected_as_invalid_params(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "placeholder")
    client, client_transport, server_transport = await setup_client()
    try:
        with pytest.raises(AppServerResponseError) as exc_info:
            await _store(client, provider="unknown-provider")
    finally:
        await client_transport.close()
        await server_transport.close()

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS


class _FakeTelemetry:
    def __init__(self, *, launch_context: Any = None, **_kwargs: Any) -> None:
        self.launch_context = launch_context
        self.sent = False
        self.custom_domain: bool | None = None

    def send_onboarding_api_key_added(self, *, custom_domain: bool = False) -> None:
        self.sent = True
        self.custom_domain = custom_domain


def _capture_telemetry(monkeypatch: pytest.MonkeyPatch) -> list[_FakeTelemetry]:
    captured: list[_FakeTelemetry] = []

    def _factory(**kwargs: Any) -> _FakeTelemetry:
        captured.append(client := _FakeTelemetry(**kwargs))
        return client

    monkeypatch.setattr(api_key_persistence, "TelemetryClient", _factory)
    monkeypatch.setattr(keyring, "set_password", lambda *args: None)
    return captured


@pytest.mark.asyncio
async def test_store_credential_emits_onboarding_telemetry_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Python timing: fired at store time, with the wizard's custom-domain
    flag and the launch context built from the handshake.
    """
    captured = _capture_telemetry(monkeypatch)
    monkeypatch.setenv("VIBE_ENABLE_TELEMETRY", "true")
    monkeypatch.setenv("MISTRAL_API_KEY", "placeholder")
    client, client_transport, server_transport = await setup_client(entrypoint="cli")
    try:
        response = await _store(client, custom_domain=True)
        # submit-choices never emits: only the credential store does.
        await client.request("setup/submit-choices", SetupSubmitChoicesParams())
    finally:
        await client_transport.close()
        await server_transport.close()

    assert response.outcome == "completed"
    assert len(captured) == 1
    assert captured[0].sent is True
    assert captured[0].custom_domain is True
    assert captured[0].launch_context is not None
    assert captured[0].launch_context.agent_entrypoint == "cli"
    assert captured[0].launch_context.client_name == "setup-test"
