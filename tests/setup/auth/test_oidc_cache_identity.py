from __future__ import annotations

import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from pydantic import SecretStr
import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server import connector_catalog
from vibe.app_server._org_config_service import DiskOrgConfigStore, OrgConfigKey
from vibe.core.config import managed_config_cache
from vibe.core.experiments import cache as eval_cache, session as experiment_session
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.experiments.manager import hash_api_key
from vibe.core.experiments.models import EvalResponse
from vibe.core.model_catalog import cache as model_cache, session as model_session
from vibe.core.model_catalog.models import ModelCatalogResponse
from vibe.core.telemetry.send import mistral_credential_cache_key
from vibe.core.utils.remote_cache import Cached
from vibe.setup.auth import _oidc_runtime
from vibe.setup.auth._oidc_config import OidcConfig, OidcError
from vibe.setup.auth._oidc_credentials import (
    OidcCredentialStore,
    OidcTenant,
    OidcTokens,
)
from vibe.utils.oidc_marker import OIDC_API_KEY


@pytest.fixture
def credentials(monkeypatch, tmp_path):
    for name in list(os.environ):
        if name.startswith("VIBE_CLI_OIDC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "organization-a")
    monkeypatch.setenv("VIBE_CLI_OIDC_WORKSPACE_ID", "workspace-a")
    monkeypatch.setenv("MISTRAL_API_KEY", "ignored-api-key")
    config = OidcConfig.from_env()
    tokens = OidcTokens(
        issuer=config.issuer,
        client_id=config.client_id,
        audience=config.audience,
        subject="subject-a",
        access_token=SecretStr("access-secret"),
        refresh_token=SecretStr("refresh-secret"),
        id_token=SecretStr("id-secret"),
        expires_at=time.time() + 3600,
        id_token_expires_at=time.time() + 3600,
    )
    current = [tokens]
    monkeypatch.setattr(OidcCredentialStore, "_load", lambda self: current[0])
    monkeypatch.setattr(
        OidcCredentialStore, "_save", lambda self, value: current.__setitem__(0, value)
    )
    monkeypatch.setattr(
        _oidc_runtime,
        "_discover_workspace",
        AsyncMock(side_effect=AssertionError("cache lookup must not discover tenant")),
    )
    monkeypatch.setattr(
        _oidc_runtime,
        "_resolve_id_token",
        lambda: pytest.fail("cache lookup must not refresh credentials"),
    )
    for module, name in (
        (managed_config_cache, "MANAGED_CONFIG_CACHE_FILE"),
        (model_cache, "MODEL_CATALOG_CACHE_FILE"),
        (eval_cache, "EXPERIMENT_EVAL_CACHE_FILE"),
    ):
        monkeypatch.setattr(module, name, SimpleNamespace(path=tmp_path / name))
    return current


def _switch(credentials, monkeypatch, field):
    tokens = credentials[0]
    assert tokens is not None
    if field == "issuer":
        monkeypatch.setenv("VIBE_CLI_OIDC_ISSUER", "https://auth.globalaegis.net")
        config = OidcConfig.from_env()
        credentials[0] = tokens.model_copy(
            update={
                "issuer": config.issuer,
                "client_id": config.client_id,
                "audience": config.audience,
            }
        )
    elif field in {"organization", "workspace"}:
        monkeypatch.setenv(f"VIBE_CLI_OIDC_{field.upper()}_ID", f"{field}-b")
    else:
        # The audience must stay a valid HTTPS origin to pass configuration.
        value = "https://audience-b.example" if field == "audience" else f"{field}-b"
        credentials[0] = tokens.model_copy(update={field: value})
        if field in {"client_id", "audience"}:
            monkeypatch.setenv(f"VIBE_CLI_OIDC_{field.upper()}", value)


@pytest.mark.parametrize(
    "field", ["subject", "issuer", "client_id", "audience", "organization", "workspace"]
)
def test_oidc_cache_identity_is_scoped_and_anonymous(credentials, monkeypatch, field):
    first = _oidc_runtime.stored_oidc_cache_key()
    assert first is not None
    assert first.startswith("cli-oidc:v1:")
    for private in ("subject-a", "organization-a", "workspace-a", "id-secret"):
        assert private not in first
    _switch(credentials, monkeypatch, field)
    assert _oidc_runtime.stored_oidc_cache_key() not in {None, first}


def test_token_rotation_preserves_identity_for_explicit_tenant(credentials):
    first = _oidc_runtime.stored_oidc_cache_key()
    credentials[0] = credentials[0].model_copy(
        update={
            "id_token": SecretStr("rotated-id-secret"),
            "access_token": SecretStr("rotated-access-secret"),
        }
    )
    assert _oidc_runtime.stored_oidc_cache_key() == first


@pytest.mark.parametrize("field", ["subject", "issuer", "organization", "workspace"])
def test_shared_disk_caches_never_load_another_oidc_identity(
    credentials, monkeypatch, field
):
    config = build_test_vibe_config(enable_telemetry=True)
    first_key = mistral_credential_cache_key(config)
    catalog = ModelCatalogResponse.model_validate({
        "models": [{"id": "model-a", "label": "Model A"}]
    })
    evaluation = EvalResponse.model_validate({
        "features": {"feature-a": {"defaultValue": "a"}}
    })
    managed_config_cache.store_cached_managed_config(first_key, 'theme = "nord"')
    model_cache.store_cached_model_catalog(config, catalog, fetched_for=first_key)
    eval_cache.store_cached_eval_response(config, evaluation, fetched_for=first_key)
    assert (
        managed_config_cache.load_cached_managed_config(first_key) == 'theme = "nord"'
    )
    assert model_cache.load_cached_model_catalog(config) == catalog
    assert eval_cache.load_cached_eval_response(config) == evaluation

    _switch(credentials, monkeypatch, field)
    second_key = mistral_credential_cache_key(config)
    assert second_key not in {None, first_key}
    assert managed_config_cache.load_cached_managed_config(second_key) is None
    assert model_cache.load_cached_model_catalog(config) is None
    assert eval_cache.load_cached_eval_response(config) is None


@pytest.mark.parametrize("field", ["subject", "organization", "workspace"])
def test_managed_policy_write_skips_replaced_session_identity(
    credentials, monkeypatch, field
):
    config = build_test_vibe_config()
    old_key = mistral_credential_cache_key(config)
    assert old_key is not None
    _switch(credentials, monkeypatch, field)
    DiskOrgConfigStore().save(
        OrgConfigKey(old_key, base_url=config.vibe_base_url, api_key=OIDC_API_KEY),
        Cached('theme = "nord"', stored_at=time.time()),
    )
    assert managed_config_cache.load_cached_managed_config(old_key) is None
    assert (
        managed_config_cache.load_cached_managed_config(
            mistral_credential_cache_key(config)
        )
        is None
    )


@pytest.mark.parametrize("missing", ["tokens", "tenant", "configuration", "subject"])
def test_unsafe_identity_bypasses_shared_caches_without_api_key_fallback(
    credentials, monkeypatch, tmp_path, missing
):
    if missing == "tokens":
        credentials[0] = None
    elif missing == "tenant":
        monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID")
    elif missing == "configuration":
        monkeypatch.setenv("VIBE_CLI_OIDC_CLIENT_ID", "different-client")
    else:
        credentials[0] = credentials[0].model_copy(update={"subject": ""})
    config = build_test_vibe_config(enable_telemetry=True)
    assert _oidc_runtime.stored_oidc_cache_key() is None
    assert mistral_credential_cache_key(config) is None
    managed_config_cache.store_cached_managed_config(None, 'theme = "nord"')
    model_cache.store_cached_model_catalog(config, ModelCatalogResponse(models=[]))
    eval_cache.store_cached_eval_response(config, EvalResponse())
    assert model_cache.load_cached_model_catalog(config) is None
    assert eval_cache.load_cached_eval_response(config) is None
    assert list(tmp_path.iterdir()) == []


def test_discovered_tenant_is_stored_for_its_workspace_selection(
    credentials, monkeypatch
):
    explicit = _oidc_runtime.stored_oidc_cache_key()
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID")
    assert _oidc_runtime.stored_oidc_cache_key() is None
    discover = AsyncMock(
        return_value=_oidc_runtime._Workspace(
            id="workspace-a", organization_id="organization-a"
        )
    )
    monkeypatch.setattr(_oidc_runtime, "_discover_workspace", discover)
    _oidc_runtime._tenant_headers(OidcConfig.from_env(), "id-secret")
    assert credentials[0].tenant == OidcTenant(
        selection="workspace-a",
        organization_id="organization-a",
        workspace_id="workspace-a",
    )
    assert _oidc_runtime.stored_oidc_cache_key() == explicit
    discover.assert_awaited_once()
    monkeypatch.setenv("VIBE_CLI_OIDC_WORKSPACE_ID", "workspace-b")
    assert _oidc_runtime.stored_oidc_cache_key() is None
    monkeypatch.setenv("VIBE_CLI_OIDC_WORKSPACE_ID", "workspace-a")
    assert _oidc_runtime.stored_oidc_cache_key() == explicit


def test_stored_tenant_survives_id_token_refresh(credentials, monkeypatch):
    """Fengyu's review: the key must not vanish when the ID token rotates, or a
    /reload right after a refresh drops the org config.
    """
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID")
    discover = AsyncMock(
        return_value=_oidc_runtime._Workspace(
            id="workspace-a", organization_id="organization-a"
        )
    )
    monkeypatch.setattr(_oidc_runtime, "_discover_workspace", discover)
    _oidc_runtime._tenant_headers(OidcConfig.from_env(), "id-secret")
    before = _oidc_runtime.stored_oidc_cache_key()
    assert before is not None
    previous = credentials[0]
    rotated = previous.model_copy(
        update={"id_token": SecretStr("rotated-id"), "tenant": None}
    )
    # What a refresh saves: new token material, the tenant carried over.
    assert OidcCredentialStore()._save_if_current(rotated, expected=previous)
    assert credentials[0].id_token.get_secret_value() == "rotated-id"
    assert _oidc_runtime.stored_oidc_cache_key() == before
    _oidc_runtime._tenant_headers(OidcConfig.from_env(), "rotated-id")
    discover.assert_awaited_once()


def test_tenant_is_not_saved_over_another_signed_in_identity(credentials, monkeypatch):
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID")
    signed_in = credentials[0]

    async def discover(config, token, selection):
        # Another account signs in while discovery runs.
        credentials[0] = signed_in.model_copy(
            update={"subject": "subject-b", "id_token": SecretStr("id-b")}
        )
        return _oidc_runtime._Workspace(
            id="workspace-a", organization_id="organization-a"
        )

    monkeypatch.setattr(_oidc_runtime, "_discover_workspace", discover)
    headers = _oidc_runtime._tenant_headers(OidcConfig.from_env(), "id-secret")
    assert headers["x-external-organization-id"] == "organization-a"
    assert credentials[0].subject == "subject-b"
    assert credentials[0].tenant is None
    assert _oidc_runtime.stored_oidc_cache_key() is None


@pytest.mark.parametrize("field", ["subject", "issuer", "organization", "workspace"])
@pytest.mark.asyncio
async def test_connector_cache_isolates_oidc_identity_in_memory_and_new_service(
    credentials, monkeypatch, tmp_path, field
):
    config = build_test_vibe_config(enable_connectors=True)
    orchestrator = FakeConfigOrchestrator(config)
    path = tmp_path / "connectors.json"
    fetch = AsyncMock(
        return_value={
            "connectors": [
                {
                    "id": "connector-a",
                    "name": "Private A",
                    "protocol": "mcp",
                    "status": {"is_ready": True},
                    "tools": [],
                }
            ]
        }
    )
    service = connector_catalog.ConnectorCatalogService(
        implicit_source_enabled=True, cache_path=path, fetch_bootstrap=fetch
    )
    first = await service.resolve_catalog(orchestrator)
    assert first is not None
    same_identity = connector_catalog.ConnectorCatalogService(
        implicit_source_enabled=True, cache_path=path, fetch_bootstrap=fetch
    )
    assert await same_identity.read_cached_catalog(orchestrator) == first
    _switch(credentials, monkeypatch, field)
    assert service.read_catalog(orchestrator).catalog is None
    assert await service.read_cached_catalog(orchestrator) is None
    new_service = connector_catalog.ConnectorCatalogService(
        implicit_source_enabled=True, cache_path=path, fetch_bootstrap=fetch
    )
    assert new_service.read_catalog(orchestrator).catalog is None
    assert await new_service.read_cached_catalog(orchestrator) is None
    fetch.return_value = {"connectors": []}
    second = await new_service.resolve_catalog(orchestrator)
    assert second is not None and second.connectors == ()
    assert second.provider_fingerprint != first.provider_fingerprint
    assert fetch.await_count == 2


@pytest.mark.asyncio
async def test_unresolved_connector_tenant_fetches_without_cache_access(
    credentials, monkeypatch, tmp_path
):
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID")
    monkeypatch.setattr(
        _oidc_runtime,
        "oidc_headers",
        AsyncMock(side_effect=OidcError("OIDC workspace discovery failed")),
    )
    config = build_test_vibe_config(enable_connectors=True)
    orchestrator = FakeConfigOrchestrator(config)
    path = tmp_path / "connectors.json"
    fetch = AsyncMock(return_value={"connectors": []})
    service = connector_catalog.ConnectorCatalogService(
        implicit_source_enabled=True, cache_path=path, fetch_bootstrap=fetch
    )
    monkeypatch.setattr(
        service._cache, "read", lambda *a, **k: pytest.fail("cache read")
    )
    monkeypatch.setattr(
        service._cache, "write", lambda *a, **k: pytest.fail("cache write")
    )
    assert await service.resolve_catalog(orchestrator) is not None
    assert await service.resolve_catalog(orchestrator) is not None
    assert fetch.await_count == 2
    assert service.read_catalog(orchestrator).catalog is None
    assert await service.read_cached_catalog(orchestrator) is None
    assert service._memory == {}
    assert not path.exists()


@pytest.mark.asyncio
async def test_first_bootstrap_discovery_publishes_resolved_cache_identity(
    credentials, monkeypatch, tmp_path
):
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID")
    orchestrator = FakeConfigOrchestrator(
        build_test_vibe_config(enable_connectors=True)
    )
    discover = AsyncMock(
        return_value=_oidc_runtime._Workspace(
            id="workspace-a", organization_id="organization-a"
        )
    )
    monkeypatch.setattr(_oidc_runtime, "_discover_workspace", discover)

    async def headers(url):
        return _oidc_runtime._tenant_headers(OidcConfig.from_env(), "id-secret")

    monkeypatch.setattr(_oidc_runtime, "oidc_headers", headers)
    fetch = AsyncMock(return_value={"connectors": []})
    path = tmp_path / "connectors.json"
    service = connector_catalog.ConnectorCatalogService(
        implicit_source_enabled=True, cache_path=path, fetch_bootstrap=fetch
    )
    pending = connector_catalog._resolve_provider(orchestrator.config)
    assert pending is not None and pending.fingerprint is None
    catalog = await service.resolve_catalog(orchestrator)
    resolved = connector_catalog._resolve_provider(orchestrator.config)
    assert resolved is not None and resolved.fingerprint is not None
    assert catalog is not None
    assert catalog.provider_fingerprint == resolved.fingerprint
    assert service.read_catalog(orchestrator).catalog == catalog
    new_service = connector_catalog.ConnectorCatalogService(
        implicit_source_enabled=True, cache_path=path, fetch_bootstrap=fetch
    )
    assert await new_service.read_cached_catalog(orchestrator) == catalog


@pytest.mark.asyncio
async def test_no_oidc_tokens_never_reads_connector_cache_or_uses_api_key(
    credentials, tmp_path
):
    credentials[0] = None
    orchestrator = FakeConfigOrchestrator(
        build_test_vibe_config(enable_connectors=True)
    )
    fetch = AsyncMock(side_effect=AssertionError("must not fetch"))
    service = connector_catalog.ConnectorCatalogService(
        implicit_source_enabled=True,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
    )
    assert await service.resolve_catalog(orchestrator) is None
    assert await service.read_cached_catalog(orchestrator) is None
    assert service.read_catalog(orchestrator).catalog is None
    fetch.assert_not_awaited()


@pytest.mark.parametrize("field", ["subject", "organization", "workspace"])
def test_disk_store_requires_the_fetching_oidc_identity(
    credentials, monkeypatch, field
):
    config = build_test_vibe_config(enable_telemetry=True)
    first_key = mistral_credential_cache_key(config)
    catalog = ModelCatalogResponse(models=[])
    # Without the key a fetch started under, an OIDC store cannot tell whose it is.
    model_cache.store_cached_model_catalog(config, catalog)
    eval_cache.store_cached_eval_response(config, EvalResponse())
    assert model_cache.load_cached_model_catalog(config) is None
    assert eval_cache.load_cached_eval_response(config) is None
    _switch(credentials, monkeypatch, field)
    model_cache.store_cached_model_catalog(config, catalog, fetched_for=first_key)
    eval_cache.store_cached_eval_response(config, EvalResponse(), fetched_for=first_key)
    assert model_cache.load_cached_model_catalog(config) is None
    assert eval_cache.load_cached_eval_response(config) is None


@pytest.mark.parametrize("field", ["subject", "organization", "workspace"])
@pytest.mark.asyncio
async def test_session_model_fetch_skips_cache_after_oidc_switch(
    credentials, monkeypatch, field
):
    config = build_test_vibe_config(experimental_enable_model_catalog=True)
    response = ModelCatalogResponse(models=[])

    async def fetch(config):
        _switch(credentials, monkeypatch, field)
        return response

    monkeypatch.setattr(model_session, "refresh_model_catalog", fetch)
    assert await model_session.fetch_and_cache_model_catalog(config) == response
    assert model_cache.load_cached_model_catalog(config) is None


@pytest.mark.parametrize("field", ["subject", "organization", "workspace"])
@pytest.mark.parametrize("entrypoint", ["initialize", "fetch"])
@pytest.mark.asyncio
async def test_session_experiment_fetch_skips_cache_after_oidc_switch(
    credentials, monkeypatch, field, entrypoint
):
    from vibe.core.telemetry.types import LaunchContext

    config = build_test_vibe_config(enable_telemetry=True)
    state = EvalResponse.model_validate({
        "features": {"feature-a": {"defaultValue": "a"}}
    })
    manager = MagicMock()
    manager.export_state.return_value = state
    manager.aclose = AsyncMock()

    async def initialize(attributes):
        _switch(credentials, monkeypatch, field)

    manager.initialize = AsyncMock(side_effect=initialize)
    attributes = MagicMock(userId="user-a")
    monkeypatch.setattr(
        experiment_session,
        "_fetch_plan_attributes",
        AsyncMock(return_value=(attributes, "PRO")),
    )
    sink = experiment_session.NullExperimentSink()
    launch = LaunchContext(
        agent_entrypoint="cli",
        agent_version="test",
        client_name="vibe",
        client_version="test",
    )
    if entrypoint == "initialize":
        await experiment_session.initialize_experiments(
            config=config,
            manager=manager,
            session_logger=sink,
            launch_context=launch,
            harness=ExperimentSurface.UNIFIED,
        )
    else:
        monkeypatch.setattr(
            experiment_session, "ExperimentManager", lambda **kwargs: manager
        )
        monkeypatch.setattr(
            experiment_session.RemoteEvalClient,
            "from_settings",
            lambda **kwargs: MagicMock(),
        )
        await experiment_session.fetch_and_cache_experiments(
            config, launch, harness=ExperimentSurface.UNIFIED
        )
    assert eval_cache.load_cached_eval_response(config) is None


@pytest.mark.asyncio
async def test_disabled_remote_features_do_not_discover_oidc_tenant(
    credentials, monkeypatch
):
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID")
    headers = AsyncMock(
        side_effect=AssertionError("disabled feature must not discover")
    )
    monkeypatch.setattr(_oidc_runtime, "oidc_headers", headers)
    assert (
        await model_session.fetch_and_cache_model_catalog(
            build_test_vibe_config(experimental_enable_model_catalog=False)
        )
        is None
    )
    config = build_test_vibe_config(enable_telemetry=True)
    config.experiments.enable = False
    monkeypatch.setattr(
        experiment_session,
        "_fetch_plan_attributes",
        AsyncMock(return_value=(MagicMock(), "PRO")),
    )
    await experiment_session.initialize_experiments(
        config=config,
        manager=MagicMock(),
        session_logger=experiment_session.NullExperimentSink(),
        launch_context=None,
        harness=ExperimentSurface.UNIFIED,
    )
    headers.assert_not_awaited()


def test_disabled_oidc_preserves_api_key_cache_keys(credentials, monkeypatch):
    monkeypatch.delenv("VIBE_CLI_OIDC_ENABLED")
    monkeypatch.setenv("MISTRAL_API_KEY", "api-key")
    monkeypatch.setattr(
        OidcCredentialStore, "_load", lambda self: pytest.fail("OIDC read")
    )
    assert mistral_credential_cache_key(build_test_vibe_config()) == hash_api_key(
        "api-key"
    )
    first = connector_catalog.connector_cache_fingerprint(
        "api-key", "https://api.mistral.ai"
    )
    assert first == connector_catalog.connector_cache_fingerprint(
        "api-key", "https://api.mistral.ai/"
    )
    assert _oidc_runtime.stored_oidc_cache_key() is None
