"""Reads outside a session fetch the remote config they find no cache for.

The new-session picker reads models through ``config/read`` and modes through
``agents/list``, both without a session. With nothing cached for the API key
(first launch, a key change, an expired entry), the read fetches the org
config, the GrowthBook eval and the model catalog, waits for them within each
fetch's limit, and answers with what they returned.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
import time
import tomllib
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

from pydantic import SecretStr
import pytest
import pytest_asyncio
import tomli_w

from tests.stubs.process_services import FakeOrgConfigServer, fake_process_services
from vibe.app_server._admin_config import OrgConfigSource
from vibe.app_server._connection_protocol import ClientInfo
from vibe.app_server._dispatch import DispatchResult, RequestFailure
from vibe.app_server._host import HostRequestHandler
import vibe.app_server._missing_layers as missing_layers
from vibe.app_server._org_config_service import ORG_CONFIG_POLICY
from vibe.app_server._process_services import ProcessServices
import vibe.app_server._runtime as runtime_module
from vibe.app_server._session_backend_port import SessionBackendHost
from vibe.app_server.protocol import (
    AgentsListParams,
    AgentsListResponse,
    ConfigReadParams,
    ConfigReadResponse,
    EmptyResponse,
    SessionHistoryListParams,
    SessionOptions,
)
from vibe.app_server.server import AppServer
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config import VibeConfigSchema
from vibe.core.config.admin_config import ManagedConfig, ManagedConfigResult
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.experiments.active import ExperimentName
from vibe.core.experiments.cache import (
    clear_cached_eval_responses,
    store_cached_eval_response,
)
from vibe.core.experiments.models import EvalResponse
from vibe.core.experiments.wait import (
    experiments_eval_failed,
    mark_experiments_eval_failed,
)
from vibe.core.model_catalog import ModelCatalogResponse, parse_model_catalog_response
from vibe.core.model_catalog.cache import store_cached_model_catalog
from vibe.core.model_catalog.wait import mark_model_catalog_failed, model_catalog_failed
from vibe.core.telemetry.send import resolve_credential_cache_key
from vibe.core.telemetry.types import LaunchContext
from vibe.core.trusted_folders import TrustedFoldersManager
from vibe.setup.auth import _oidc_runtime
from vibe.setup.auth._oidc_config import OidcConfig
from vibe.setup.auth._oidc_credentials import (
    OidcCredentialStore,
    OidcTenant,
    OidcTokens,
)


@pytest.fixture
def oidc_credentials(monkeypatch):
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.delenv("VIBE_CLI_OIDC_ORGANIZATION_ID", raising=False)
    monkeypatch.delenv("VIBE_CLI_OIDC_WORKSPACE_ID", raising=False)
    config = OidcConfig.from_env()
    current = [
        OidcTokens(
            issuer=config.issuer,
            client_id=config.client_id,
            audience=config.audience,
            subject="user-a",
            access_token=SecretStr("access-a"),
            refresh_token=SecretStr("refresh-a"),
            id_token=SecretStr("id-a"),
            expires_at=time.time() + 3600,
            id_token_expires_at=time.time() + 3600,
        )
    ]
    monkeypatch.setattr(OidcCredentialStore, "_load", lambda self: current[0])

    async def headers(url):
        tokens = current[0]
        assert tokens is not None
        tenant = OidcTenant(
            selection="",
            organization_id="org-" + tokens.subject,
            workspace_id="workspace-" + tokens.subject,
        )
        # What discovery does: the tenant is stored with the session.
        current[0] = tokens.model_copy(update={"tenant": tenant})
        return {
            "Authorization": "Bearer " + tokens.id_token.get_secret_value(),
            "x-external-organization-id": tenant.organization_id,
            "x-external-workspace-id": tenant.workspace_id,
        }

    resolve = AsyncMock(side_effect=headers)
    monkeypatch.setattr(_oidc_runtime, "oidc_headers", resolve)
    return current, resolve


_CLIENT = ClientInfo(name="vibe-desktop", version="9.9.9")
_ORG_TOML = 'default_agent = "ask"\n'
_LIMIT = 0.2


def _catalog() -> ModelCatalogResponse:
    parsed = parse_model_catalog_response({
        "models": [
            {
                "id": "catalog-model",
                "label": "Catalog model",
                "capabilities": {"function_calling": True},
            }
        ]
    })
    assert parsed is not None
    return parsed


def _smart_approve_eval() -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {
            ExperimentName.SMART_APPROVE.value: {"defaultValue": True, "rules": []}
        }
    })


class _Remote:
    """The three endpoints: answering at once or after a delay, or hanging
    until released.
    """

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        org_server: FakeOrgConfigServer,
        *,
        hang: bool = False,
        delays: dict[str, float] | None = None,
        org_toml: str = _ORG_TOML,
    ):
        self.hang = hang
        self.delays = delays or {}
        self.org_toml = org_toml
        self.release = asyncio.Event()
        self.calls = {"org": 0, "eval": 0, "catalog": 0}
        self.client_names: list[str | None] = []
        org_server.respond = self._org
        monkeypatch.setattr(missing_layers, "fetch_and_cache_experiments", self._eval)
        monkeypatch.setattr(
            missing_layers, "fetch_and_cache_model_catalog", self._catalog
        )

    async def _block(self, source: str) -> bool:
        self.calls[source] += 1
        if self.hang:
            await self.release.wait()
        await asyncio.sleep(self.delays.get(source, 0))
        return not self.hang

    async def _org(self) -> ManagedConfigResult:
        await self._block("org")
        return ManagedConfigResult(
            config=ManagedConfig(state="enabled", toml=self.org_toml)
        )

    async def _eval(
        self, config: VibeConfigSchema, launch_context: LaunchContext, **_: Any
    ) -> None:
        self.client_names.append(launch_context.client_name)
        if await self._block("eval"):
            store_cached_eval_response(
                config,
                _smart_approve_eval(),
                fetched_for=await resolve_credential_cache_key(config),
            )

    async def _catalog(self, config: Any) -> ModelCatalogResponse | None:
        if not await self._block("catalog"):
            return None
        store_cached_model_catalog(
            config, _catalog(), fetched_for=await resolve_credential_cache_key(config)
        )
        return _catalog()


def _set_local_config(config_dir: Path, **values: object) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config.update(values)
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


@pytest.fixture
def remote_config_enabled(config_dir: Path) -> None:
    _set_local_config(
        config_dir, enable_telemetry=True, experimental_enable_model_catalog=True
    )


def _set_limits(monkeypatch: pytest.MonkeyPatch, seconds: float) -> None:
    monkeypatch.setattr("vibe.core.experiments.wait.EVAL_WAIT_CAP_SECONDS", seconds)
    monkeypatch.setattr(
        "vibe.core.model_catalog.wait.MODEL_CATALOG_WAIT_CAP_SECONDS", seconds
    )


@pytest.fixture
def short_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_limits(monkeypatch, _LIMIT)


@pytest.fixture
def org_limit() -> float:
    """How long the org config's first fetch is waited for, and may run."""
    return _LIMIT


@pytest_asyncio.fixture
async def process_services(
    org_server: FakeOrgConfigServer, org_limit: float
) -> AsyncIterator[ProcessServices]:
    services = fake_process_services(
        org_server,
        policy=replace(ORG_CONFIG_POLICY, fetch_timeout_s=org_limit),
        first_wait_s=org_limit,
    )
    yield services
    await services.close()


def _handler(*, process_services: ProcessServices) -> HostRequestHandler:
    return HostRequestHandler(
        HarnessFilesManager(sources=("user",)), org_config=process_services.org_config
    )


async def _read_config(handler: HostRequestHandler) -> ConfigReadResponse:
    result = await handler.dispatch(
        "config/read",
        ConfigReadParams().model_dump(mode="json", by_alias=True),
        client_info=_CLIENT,
    )
    return cast(ConfigReadResponse, result.response)


async def _list_agents(handler: HostRequestHandler) -> AgentsListResponse:
    result = await handler.dispatch(
        "agents/list",
        AgentsListParams().model_dump(mode="json", by_alias=True),
        client_info=_CLIENT,
    )
    return cast(AgentsListResponse, result.response)


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_stored_oidc_without_tenant_resolves_then_fetches_picker_layers(
    monkeypatch, oidc_credentials, process_services, org_server
):
    _, resolve = oidc_credentials
    remote = _Remote(monkeypatch, org_server)
    handler = _handler(process_services=process_services)
    view = (await _read_config(handler)).config
    assert view.default_agent == "ask"
    assert "catalog-model" in {model.alias for model in view.models}
    assert remote.calls == {"org": 1, "eval": 1, "catalog": 1}
    resolve.assert_awaited_once()
    await _read_config(handler)
    resolve.assert_awaited_once()
    assert remote.calls == {"org": 1, "eval": 1, "catalog": 1}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_shared_cache_application_resolves_session_tenant(
    monkeypatch, oidc_credentials
):
    from vibe.app_server._cached_layers import apply_cached_layers
    from vibe.core.config import build_default_orchestrator
    from vibe.core.telemetry.send import mistral_credential_cache_key

    _, resolve = oidc_credentials
    orchestrator = await build_default_orchestrator(
        harness_files=HarnessFilesManager(sources=("user",))
    )
    assert mistral_credential_cache_key(orchestrator.config) is None
    key, _, _ = await apply_cached_layers(orchestrator, None)
    assert key is not None
    assert key.cache_key == mistral_credential_cache_key(orchestrator.config)
    resolve.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_oidc_tenant_resolution_timeout_keeps_local_defaults(
    monkeypatch, oidc_credentials, process_services, org_server
):
    _, resolve = oidc_credentials

    async def blocked(url):
        await asyncio.Event().wait()

    resolve.side_effect = blocked
    monkeypatch.setattr("vibe.core.telemetry.send._OIDC_TENANT_TIMEOUT_SECONDS", 0.01)
    remote = _Remote(monkeypatch, org_server)
    view = (await _read_config(_handler(process_services=process_services))).config
    assert view.default_agent != "ask"
    assert remote.calls == {"org": 0, "eval": 0, "catalog": 0}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_tenant_timeout_does_not_wait_for_discovery_worker(
    monkeypatch, oidc_credentials
):
    from threading import Event

    from vibe.app_server._cached_layers import apply_cached_layers
    from vibe.core.config import build_default_orchestrator

    _, resolve = oidc_credentials
    started = asyncio.Event()
    release = Event()
    loop = asyncio.get_running_loop()

    def discover():
        with _oidc_runtime._tenant_lock:
            loop.call_soon_threadsafe(started.set)
            assert release.wait(2)

    async def blocked(url):
        await asyncio.to_thread(discover)

    resolve.side_effect = blocked
    monkeypatch.setattr("vibe.core.telemetry.send._OIDC_TENANT_TIMEOUT_SECONDS", 0.01)
    orchestrator = await build_default_orchestrator(
        harness_files=HarnessFilesManager(sources=("user",))
    )
    pending = asyncio.create_task(apply_cached_layers(orchestrator, None))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert await asyncio.wait_for(pending, 0.2) == (None, None, None)
        # A repeated cache lookup must not stall the app-server loop either.
        assert _oidc_runtime.stored_oidc_cache_key() is None
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["org"])
@pytest.mark.usefixtures("remote_config_enabled")
async def test_account_switch_during_picker_fetch_never_caches_previous_response(
    monkeypatch, oidc_credentials, source, process_services, org_server
):
    from vibe.core.config.managed_config_cache import load_cached_managed_config
    from vibe.core.experiments.cache import load_cached_eval_response
    from vibe.core.model_catalog.cache import load_cached_model_catalog
    from vibe.core.telemetry.send import mistral_credential_cache_key

    current, resolve = oidc_credentials
    remote = _Remote(monkeypatch, org_server)
    started, release = asyncio.Event(), asyncio.Event()
    original = remote._block

    async def blocked(name):
        if name == source:
            started.set()
            await release.wait()
        return await original(name)

    monkeypatch.setattr(remote, "_block", blocked)
    pending = asyncio.create_task(
        _read_config(_handler(process_services=process_services))
    )
    await asyncio.wait_for(started.wait(), 2)
    tokens = current[0]
    assert tokens is not None
    current[0] = tokens.model_copy(
        update={"subject": "user-b", "id_token": SecretStr("id-b")}
    )
    await resolve("https://api.mistral.ai/v1")
    release.set()
    await pending
    config = VibeConfigSchema()
    assert load_cached_managed_config(mistral_credential_cache_key(config)) is None
    assert load_cached_eval_response(config) is None
    assert load_cached_model_catalog(config) is None


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_a_read_with_nothing_cached_answers_with_the_fetched_config(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    remote = _Remote(monkeypatch, org_server)
    handler = _handler(process_services=process_services)

    view = (await _read_config(handler)).config
    agents = await _list_agents(handler)

    assert "catalog-model" in {model.alias for model in view.models}
    assert view.default_agent == "ask"
    assert agents.active.name == "ask"
    assert "smart-approve" in {agent.name for agent in agents.agents}
    # The eval is evaluated for the client that asked, as a session's is.
    assert remote.client_names == ["vibe-desktop"]
    # The second read found the caches the first one wrote.
    assert remote.calls == {"org": 1, "eval": 1, "catalog": 1}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled", "short_limits")
async def test_hanging_endpoints_answer_local_defaults_and_are_not_waited_for_again(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    remote = _Remote(monkeypatch, org_server, hang=True)
    handler = _handler(process_services=process_services)

    started = time.monotonic()
    first = (await _read_config(handler)).config
    first_duration = time.monotonic() - started
    # GrowthBook's and the catalog's own limits start once the org config's
    # ends, so all three have failed or hit their limit by now.
    await asyncio.sleep(2 * _LIMIT)
    # Any wait now would take seconds, far above the read's own cost.
    _set_limits(monkeypatch, 5.0)
    started = time.monotonic()
    second = (await _read_config(handler)).config
    second_duration = time.monotonic() - started
    remote.release.set()

    assert first_duration < 1.5
    assert first.default_agent != "ask"
    assert "catalog-model" not in {model.alias for model in first.models}
    # Every source failed or hit its limit: the second read neither fetches
    # nor waits.
    assert second_duration < 2.5
    assert remote.calls == {"org": 1, "eval": 1, "catalog": 1}
    assert second.default_agent == first.default_agent


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_a_read_with_everything_cached_fetches_nothing(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    remote = _Remote(monkeypatch, org_server)
    await _read_config(_handler(process_services=process_services))
    remote.calls = {"org": 0, "eval": 0, "catalog": 0}

    await _read_config(_handler(process_services=process_services))

    assert remote.calls == {"org": 0, "eval": 0, "catalog": 0}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_another_api_key_fetches_for_itself(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    remote = _Remote(monkeypatch, org_server)
    handler = _handler(process_services=process_services)
    await _read_config(handler)

    monkeypatch.setenv("MISTRAL_API_KEY", "another-key")
    await _read_config(handler)

    assert remote.calls == {"org": 2, "eval": 2, "catalog": 2}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_concurrent_reads_share_one_fetch(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    remote = _Remote(monkeypatch, org_server)
    handler = _handler(process_services=process_services)

    views = await asyncio.gather(_read_config(handler), _read_config(handler))

    assert remote.calls == {"org": 1, "eval": 1, "catalog": 1}
    assert [view.config.default_agent for view in views] == ["ask", "ask"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("short_limits")
async def test_a_session_start_after_a_failed_org_config_read_waits_for_the_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, org_server: FakeOrgConfigServer
) -> None:
    # A failed fetch is not retried before its delay: a session opened
    # meanwhile neither waits nor fetches, and the next one after it does.
    clock = [1_000_000.0]
    services = fake_process_services(
        org_server,
        policy=replace(ORG_CONFIG_POLICY, fetch_timeout_s=_LIMIT),
        first_wait_s=_LIMIT,
        clock=lambda: clock[0],
    )
    remote = _Remote(monkeypatch, org_server, hang=True)
    try:
        await _read_config(_handler(process_services=services))
        await asyncio.sleep(2 * _LIMIT)
        assert remote.calls["org"] == 1
        remote.hang = False
        remote.release.set()
        process = runtime_module.HarnessProcess(services=services)

        during = await process._build_session_config(
            SessionOptions(cwd=str(tmp_path)), org_config=OrgConfigSource.ENSURED
        )
        clock[0] += ORG_CONFIG_POLICY.retry_after_s
        after = await process._build_session_config(
            SessionOptions(cwd=str(tmp_path)), org_config=OrgConfigSource.ENSURED
        )

        assert during.config_orchestrator.config.default_agent != "ask"
        assert remote.calls["org"] == 2
        assert after.config_orchestrator.config.default_agent == "ask"
    finally:
        await services.close()


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_reads_the_picker_does_not_make_fetch_nothing(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    remote = _Remote(monkeypatch, org_server)

    # The session list builds config too, before finding no such session.
    with pytest.raises(RequestFailure):
        await _handler(process_services=process_services).dispatch(
            "session/history/list",
            SessionHistoryListParams(session_id="missing").model_dump(
                mode="json", by_alias=True
            ),
            client_info=_CLIENT,
        )

    assert remote.calls == {"org": 0, "eval": 0, "catalog": 0}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
@pytest.mark.parametrize("org_limit", [1.0])
async def test_a_slow_org_config_is_not_counted_against_the_other_fetches(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    # The org config takes most of the read's limit, so the read answers
    # before GrowthBook and the catalog land. Each lands within its own limit,
    # so neither is recorded as failed: a session start still waits for them.
    # Read limit 1 s; GrowthBook and the catalog land at 1.3 s, 0.4 s before
    # their own limits, which start once the org config is in at 0.7 s.
    _set_limits(monkeypatch, 1.0)
    remote = _Remote(
        monkeypatch, org_server, delays={"org": 0.7, "eval": 0.6, "catalog": 0.6}
    )
    handler = _handler(process_services=process_services)

    first = (await _read_config(handler)).config
    await asyncio.sleep(0.6)
    second = (await _read_config(handler)).config

    assert "catalog-model" not in {model.alias for model in first.models}
    assert not experiments_eval_failed()
    assert not model_catalog_failed()
    assert "catalog-model" in {model.alias for model in second.models}
    assert remote.calls == {"org": 1, "eval": 1, "catalog": 1}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_a_config_read_next_to_an_attached_session_fetches_nothing(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    # The terminal UI reads its config on the connection its session is
    # attached to; that session is already fetching the same config.
    remote = _Remote(monkeypatch, org_server)

    await _handler(process_services=process_services).dispatch(
        "config/read",
        ConfigReadParams().model_dump(mode="json", by_alias=True),
        session_id="attached-session",
        client_info=_CLIENT,
    )

    assert remote.calls == {"org": 0, "eval": 0, "catalog": 0}


@pytest.mark.asyncio
async def test_the_fetches_follow_the_org_config(
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    # Local config has the catalog off and telemetry on; the org config turns
    # the catalog on and telemetry off, so no eval is sent.
    _set_local_config(
        config_dir, enable_telemetry=True, experimental_enable_model_catalog=False
    )
    remote = _Remote(
        monkeypatch,
        org_server,
        org_toml="experimental_enable_model_catalog = true\nenable_telemetry = false\n",
    )

    view = (await _read_config(_handler(process_services=process_services))).config

    assert "catalog-model" in {model.alias for model in view.models}
    assert remote.calls == {"org": 1, "eval": 0, "catalog": 1}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_only_the_source_without_a_cache_is_fetched(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    remote = _Remote(monkeypatch, org_server)
    handler = _handler(process_services=process_services)
    await _read_config(handler)
    clear_cached_eval_responses()

    await _read_config(handler)

    assert remote.calls == {"org": 1, "eval": 2, "catalog": 1}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_a_source_that_already_failed_is_not_fetched(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    # The session path recorded these failures; the read shares them.
    remote = _Remote(monkeypatch, org_server)
    mark_experiments_eval_failed()
    mark_model_catalog_failed()

    await _read_config(_handler(process_services=process_services))

    assert remote.calls == {"org": 1, "eval": 0, "catalog": 0}


class _RecordingHost(HostRequestHandler):
    def __init__(self, process_services: ProcessServices) -> None:
        super().__init__(
            HarnessFilesManager(sources=("user",)),
            org_config=process_services.org_config,
        )
        self.dispatched: list[tuple[str, str | None, ClientInfo | None]] = []

    async def dispatch(
        self,
        method: str,
        raw_params: dict[str, Any],
        *,
        session_id: str | None = None,
        worktree_trust_store: TrustedFoldersManager | None = None,
        client_info: ClientInfo | None = None,
    ) -> DispatchResult:
        self.dispatched.append((method, session_id, client_info))
        return DispatchResult(EmptyResponse())


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["config/read", "agents/list"])
@pytest.mark.parametrize("attached", [None, "attached-session"])
async def test_the_server_tells_picker_reads_whether_a_session_is_attached(
    method: str, attached: str | None, process_services: ProcessServices
) -> None:
    host = _RecordingHost(process_services)
    _, server_transport = memory_transport_pair()
    server = AppServer(
        server_transport,
        session_backend_host_factory=lambda _: cast(SessionBackendHost, object()),
        host_handler=host,
    )
    server._client_info = _CLIENT
    if attached is not None:
        server._root = cast(Any, MagicMock(session_id=attached))

    await server._dispatch_request(method, {})

    assert host.dispatched == [(method, attached, _CLIENT)]


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_an_org_config_the_folder_rejects_is_cached_and_rejected_per_read(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    # The org toml parses but this folder's merged config rejects it (the
    # string where a bool is expected). It is cached anyway: another folder
    # may accept it, and every folder re-validates the cached copy at apply
    # time, as a session does.
    remote = _Remote(
        monkeypatch,
        org_server,
        org_toml='default_agent = "ask"\nenable_telemetry = "maybe"\n',
    )
    handler = _handler(process_services=process_services)

    first = (await _read_config(handler)).config
    second = (await _read_config(handler)).config

    assert remote.calls["org"] == 1
    assert first.default_agent != "ask"
    assert second.default_agent != "ask"


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_an_unparseable_org_config_stops_later_reads_fetching(
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    org_server: FakeOrgConfigServer,
) -> None:
    # A malformed payload is the remote's, not the folder's: it will not
    # improve within the process, so later reads stop fetching it.
    remote = _Remote(monkeypatch, org_server, org_toml="default_agent =\n")
    handler = _handler(process_services=process_services)

    await _read_config(handler)
    await _read_config(handler)

    assert remote.calls["org"] == 1
