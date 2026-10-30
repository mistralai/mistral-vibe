from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import logging
from pathlib import Path
import threading
from threading import Event
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import respx

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._dispatch import DispatchResult, RequestFailure
from vibe.app_server._session_backend_port import (
    ConnectorAuthRequest,
    ConnectorConnectRequest,
    ResolvedConnector,
    ResolvedConnectorCatalog,
    ResolvedConnectorSelection,
    ResolvedConnectorTool,
    SessionBackend,
    SessionBackendError,
    SessionConnectorSourceState,
    SessionConnectorState,
    SessionConnectorToolDescriptor,
)
import vibe.app_server.connector_catalog as connector_catalog
from vibe.app_server.connector_catalog import (
    ConnectorCatalogCache,
    ConnectorCatalogService,
    ConnectorCatalogUnavailableError,
    ConnectorCatalogValidationError,
    connector_cache_fingerprint,
    connector_source_enabled,
    connector_tool_enabled,
    resolve_connector_selection,
)
from vibe.app_server.models import AccountPlanKind
from vibe.app_server.protocol import ConnectorAuthRequiredParams, ProtocolErrorCode
from vibe.core.config import ConnectorConfig, VibeConfigSchema
from vibe.core.identity import IdentityResult
from vibe.setup.auth.whoami import WhoAmIResult, store_cached_whoami


@pytest.mark.asyncio
async def test_manage_connectors_url_builds_console_share_link(monkeypatch) -> None:
    config = build_test_vibe_config(enable_connectors=True)
    provider = connector_catalog._ConnectorProvider(
        fingerprint="fp", base_url="https://api.mistral.ai", api_key="k"
    )
    identity = IdentityResult.model_validate({
        "id": "user-1",
        "organization": {"id": "org-1", "name": "Org"},
        "workspace": {"id": "ws-1", "name": "Workspace"},
    })
    monkeypatch.setattr(
        connector_catalog, "_resolve_provider", lambda _config: provider
    )
    resolve = AsyncMock(return_value=identity)
    monkeypatch.setattr(connector_catalog._IDENTITY_CACHE, "resolve", resolve)

    url = await connector_catalog._manage_connectors_url(config)

    # Identity must be fetched from the versioned API base (.../v1/users/me),
    # not the bare connectors bootstrap server root.
    assert resolve.await_args is not None
    mistral_provider = config.get_mistral_provider()
    assert mistral_provider is not None
    identity_base = resolve.await_args.kwargs["base_url"]
    assert identity_base == mistral_provider.api_base
    assert identity_base.rstrip("/").endswith("/v1")

    assert url is not None
    assert url.startswith(f"{config.console_base_url}/build/connectors?shareContext=")
    from urllib.parse import parse_qs, urlsplit

    share_context = parse_qs(urlsplit(url).query)["shareContext"][0]
    assert json.loads(share_context) == {
        "organizationId": "org-1",
        "workspaceId": "ws-1",
    }


@pytest.mark.asyncio
async def test_manage_connectors_url_none_without_mistral_provider(monkeypatch) -> None:
    config = build_test_vibe_config(enable_connectors=True)
    monkeypatch.setattr(connector_catalog, "_resolve_provider", lambda _config: None)

    assert await connector_catalog._manage_connectors_url(config) is None


@pytest.mark.asyncio
async def test_manage_connectors_url_none_when_identity_incomplete(monkeypatch) -> None:
    config = build_test_vibe_config(enable_connectors=True)
    provider = connector_catalog._ConnectorProvider(
        fingerprint="fp", base_url="https://api.mistral.ai", api_key="k"
    )
    identity = IdentityResult.model_validate({
        "id": "user-1",
        "organization": {"id": "org-1", "name": "Org"},
    })
    monkeypatch.setattr(
        connector_catalog, "_resolve_provider", lambda _config: provider
    )
    monkeypatch.setattr(
        connector_catalog._IDENTITY_CACHE, "resolve", AsyncMock(return_value=identity)
    )

    assert await connector_catalog._manage_connectors_url(config) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("can_access_private_resources", "expected"),
    [(False, True), (True, False), (None, False)],
)
async def test_private_connectors_hidden_from_account_context(
    monkeypatch, can_access_private_resources: bool | None, expected: bool
) -> None:
    config = build_test_vibe_config(enable_connectors=True)
    provider = connector_catalog._ConnectorProvider(
        fingerprint="fp", base_url="https://api.mistral.ai", api_key="k"
    )
    monkeypatch.setattr(
        connector_catalog, "_resolve_provider", lambda _config: provider
    )
    whoami = WhoAmIResult(
        plan_type=AccountPlanKind.CHAT,
        plan_name="FREE",
        can_access_private_resources=can_access_private_resources,
    )
    resolve = AsyncMock(return_value=whoami)
    monkeypatch.setattr(connector_catalog._WHOAMI_CACHE, "resolve", resolve)
    monkeypatch.setattr(connector_catalog, "_WHOAMI_FAILED_AT", {})

    assert await connector_catalog._private_connectors_hidden(config) is expected
    assert resolve.await_args is not None
    assert resolve.await_args.kwargs["base_url"] == config.console_base_url
    assert resolve.await_args.kwargs["timeout"] == 2.0


@pytest.mark.asyncio
async def test_private_connectors_hidden_retries_whoami_only_after_cooldown(
    monkeypatch,
) -> None:
    config = build_test_vibe_config(enable_connectors=True)
    provider = connector_catalog._ConnectorProvider(
        fingerprint="fp", base_url="https://api.mistral.ai", api_key="k"
    )
    monkeypatch.setattr(
        connector_catalog, "_resolve_provider", lambda _config: provider
    )
    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(connector_catalog._WHOAMI_CACHE, "resolve", resolve)
    failed_at: dict[tuple[str, str], float] = {}
    monkeypatch.setattr(connector_catalog, "_WHOAMI_FAILED_AT", failed_at)

    assert await connector_catalog._private_connectors_hidden(config) is False
    assert await connector_catalog._private_connectors_hidden(config) is False
    assert resolve.await_count == 1

    key = (config.console_base_url, "k")
    failed_at[key] -= connector_catalog._WHOAMI_RETRY_SECONDS + 1
    resolve.return_value = WhoAmIResult(
        plan_type=AccountPlanKind.API,
        plan_name="FREE",
        can_access_private_resources=False,
    )

    assert await connector_catalog._private_connectors_hidden(config) is True
    assert resolve.await_count == 2
    assert key not in failed_at


@pytest.mark.asyncio
async def test_private_connectors_hidden_reads_disk_cache_during_cooldown(
    monkeypatch,
) -> None:
    config = build_test_vibe_config(enable_connectors=True)
    provider = connector_catalog._ConnectorProvider(
        fingerprint="fp", base_url="https://api.mistral.ai", api_key="k"
    )
    monkeypatch.setattr(
        connector_catalog, "_resolve_provider", lambda _config: provider
    )
    resolve = AsyncMock(return_value=None)
    monkeypatch.setattr(connector_catalog._WHOAMI_CACHE, "resolve", resolve)
    failed_at: dict[tuple[str, str], float] = {}
    monkeypatch.setattr(connector_catalog, "_WHOAMI_FAILED_AT", failed_at)

    assert await connector_catalog._private_connectors_hidden(config) is False
    store_cached_whoami(
        "k",
        WhoAmIResult(
            plan_type=AccountPlanKind.API,
            plan_name="FREE",
            can_access_private_resources=False,
        ),
    )

    assert await connector_catalog._private_connectors_hidden(config) is True
    assert resolve.await_count == 1
    assert failed_at == {}


@pytest.mark.asyncio
async def test_private_connectors_hidden_false_without_mistral_provider(
    monkeypatch,
) -> None:
    config = build_test_vibe_config(enable_connectors=True)
    monkeypatch.setattr(connector_catalog, "_resolve_provider", lambda _config: None)

    assert await connector_catalog._private_connectors_hidden(config) is False


def test_resolve_provider_gates_document_library_connector(monkeypatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    disabled = connector_catalog._resolve_provider(
        build_test_vibe_config(enable_connectors=True)
    )
    assert disabled is not None
    assert disabled.builtins == ("web_search",)

    enabled = connector_catalog._resolve_provider(
        build_test_vibe_config(
            enable_connectors=True, experimental_enable_document_library_connector=True
        )
    )
    assert enabled is not None
    assert enabled.builtins == ("web_search", "document_library")
    # The gated builtins must fold into the cache key so the two states never
    # collide on disk.
    assert disabled.fingerprint != enabled.fingerprint


def _connector(
    *,
    connector_id: str = "connector-1",
    name: str = "wiki",
    display_name: str | None = None,
    ready: bool = True,
    protocol: str | None = "mcp",
    tool_name: str = "search",
    bootstrap_errors: list[str] | None = None,
) -> dict[str, object]:
    connector: dict[str, object] = {
        "id": connector_id,
        "name": name,
        "status": {"is_ready": ready},
        "tools": [
            {
                "name": tool_name,
                "description": "Search docs",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                },
            }
        ],
        "bootstrap_errors": bootstrap_errors,
    }
    if display_name is not None:
        connector["display_name"] = display_name
    if protocol is not None:
        connector["protocol"] = protocol
    return connector


def test_resolve_uses_backend_display_name_but_stable_alias() -> None:
    catalog = connector_catalog._resolve_catalog(
        {"connectors": [_connector(name="wiki", display_name="Company Wiki")]},
        "fingerprint",
    )

    connector = catalog.connectors[0]
    assert connector.display_name == "Company Wiki"
    # Alias stays derived from name, not the (possibly localized) display_name,
    # so connector ids/config/tool names don't churn.
    assert connector.alias == "wiki"


def test_resolve_falls_back_when_display_name_absent() -> None:
    """Older backends omit display_name; parsing tolerates it."""
    catalog = connector_catalog._resolve_catalog(
        {"connectors": [_connector(name="wiki")]}, "fingerprint"
    )

    connector = catalog.connectors[0]
    assert connector.display_name == "wiki"
    assert connector.alias == "wiki"


def test_connector_cache_round_trip_preserves_display_name() -> None:
    live = connector_catalog._resolve_catalog(
        {"connectors": [_connector(name="wiki", display_name="Company Wiki")]},
        "fingerprint",
    )
    entry = connector_catalog._cache_entry(live, stored_at=1_000)
    hit = connector_catalog._parse_cache_entry("fingerprint", entry, now=1_000)

    assert hit is not None
    cached = hit.catalog.connectors[0]
    assert cached.alias == "wiki"
    assert cached.display_name == "Company Wiki"
    assert hit.catalog.revision == live.revision


@pytest.mark.asyncio
async def test_connector_bootstrap_processing_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The blocking HTTP setup and response parsing do not stall the caller's loop."""
    # Prepare
    event_loop_thread = threading.get_ident()
    construction_threads: list[int] = []
    parsing_threads: list[int] = []
    response = MagicMock()
    response.json.side_effect = lambda: (
        parsing_threads.append(threading.get_ident()),
        {"connectors": []},
    )[1]
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.get = AsyncMock(return_value=response)

    def build_client() -> MagicMock:
        construction_threads.append(threading.get_ident())
        return client

    monkeypatch.setattr(connector_catalog, "_build_bootstrap_http_client", build_client)

    # Do
    payload = await connector_catalog._fetch_bootstrap(
        "https://api.example.test", "secret"
    )

    # Assert
    assert payload == {"connectors": []}
    client.get.assert_awaited_once_with(
        "https://api.example.test/v1/connectors/bootstrap",
        headers={"Authorization": "Bearer secret"},
        params={
            # Default omits the gated document_library connector.
            "include_auth_actionable_connectors": "true",
            "builtin_connectors": ("web_search",),
            "supports_mcp": "true",
        },
    )
    assert construction_threads[0] != event_loop_thread
    assert parsing_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_connector_catalog_trusts_server_capability_filter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        return {
            "connectors": [
                _connector(connector_id="mcp", name="MCP"),
                _connector(connector_id="legacy", name="Legacy", protocol=None),
                _connector(connector_id="empty", name="Empty", protocol=""),
                _connector(connector_id="http", name="HTTP", protocol="http"),
            ]
        }

    catalog = await ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
    ).resolve_catalog(_orchestrator())

    assert catalog is not None
    assert [connector.raw_id for connector in catalog.connectors] == [
        "empty",
        "http",
        "legacy",
        "mcp",
    ]


@pytest.mark.asyncio
async def test_connector_catalog_resolution_runs_off_the_event_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Connector validation and projection do not stall the caller's event loop."""
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    event_loop_thread = threading.get_ident()
    resolution_threads: list[int] = []
    resolve_catalog = connector_catalog._resolve_catalog

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        return {"connectors": [_connector()]}

    def record_resolution(
        payload: object, provider_fingerprint: str
    ) -> ResolvedConnectorCatalog:
        resolution_threads.append(threading.get_ident())
        return resolve_catalog(payload, provider_fingerprint)

    monkeypatch.setattr(connector_catalog, "_resolve_catalog", record_resolution)
    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
    )

    # Do
    catalog = await service.resolve_catalog(_orchestrator())

    # Assert
    assert catalog is not None
    assert resolution_threads[0] != event_loop_thread


@pytest.mark.asyncio
async def test_connector_catalog_async_read_keeps_keyring_and_disk_off_event_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Opening the cached catalog does not perform blocking reads on the caller's loop."""
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    event_loop_thread = threading.get_ident()
    provider_threads: list[int] = []
    cache_threads: list[int] = []
    resolve_provider = connector_catalog._resolve_provider

    def record_provider(config: VibeConfigSchema):
        provider_threads.append(threading.get_ident())
        return resolve_provider(config)

    def read_cache(_fingerprint: str, *, now: int):
        del now
        cache_threads.append(threading.get_ident())
        return None

    service = ConnectorCatalogService(
        implicit_source_enabled=False, cache_path=tmp_path / "connectors.json"
    )
    monkeypatch.setattr(connector_catalog, "_resolve_provider", record_provider)
    monkeypatch.setattr(service._cache, "read", read_cache)

    # Do
    result = await service._read_catalog_async(_orchestrator())

    # Assert
    assert result.catalog is None
    assert provider_threads[0] != event_loop_thread
    assert cache_threads[0] != event_loop_thread


def _orchestrator(*, connectors: list[ConnectorConfig] | None = None):
    return FakeConfigOrchestrator(
        build_test_vibe_config(enable_connectors=True, connectors=connectors or [])
    )


def _v2_entry(payload: object, *, stored_at: int = 1_000) -> dict[str, object]:
    return {"format": 2, "stored_at": stored_at, "payload": payload}


def _selection_catalog() -> ResolvedConnectorCatalog:
    return ResolvedConnectorCatalog(
        provider_fingerprint="provider",
        revision="catalog",
        connectors=(
            ResolvedConnector(
                raw_id="github/raw",
                alias="github",
                display_name="GitHub",
                ready=True,
                auth_action="none",
                tools=(
                    ResolvedConnectorTool(
                        raw_name="search", description="Search", input_schema={}
                    ),
                    ResolvedConnectorTool(
                        raw_name="write", description="Write", input_schema={}
                    ),
                    ResolvedConnectorTool(
                        raw_name="delete", description="Delete", input_schema={}
                    ),
                ),
            ),
            ResolvedConnector(
                raw_id="linear/raw",
                alias="linear",
                display_name="Linear",
                ready=True,
                auth_action="none",
                tools=(),
            ),
            ResolvedConnector(
                raw_id="slack/raw",
                alias="slack",
                display_name="Slack",
                ready=True,
                auth_action="none",
                tools=(
                    ResolvedConnectorTool(
                        raw_name="search", description="Search", input_schema={}
                    ),
                ),
            ),
        ),
    )


def _cache_catalog(fingerprint: str, alias: str) -> ResolvedConnectorCatalog:
    return ResolvedConnectorCatalog(
        provider_fingerprint=fingerprint,
        revision=f"revision-{alias}",
        connectors=(
            ResolvedConnector(
                raw_id=f"raw-{alias}",
                alias=alias,
                display_name=alias,
                ready=True,
                auth_action="none",
                tools=(),
            ),
        ),
    )


class _BusyConnectorControl:
    session_id = "session-1"

    def __init__(
        self,
        orchestrator: FakeConfigOrchestrator[VibeConfigSchema],
        state: SessionConnectorState,
    ) -> None:
        self.connector_config_orchestrator = orchestrator
        self.state = state
        self.busy = True
        self.attempts: list[
            tuple[ResolvedConnectorCatalog, ResolvedConnectorSelection, bool]
        ] = []

    async def read_connectors(self) -> SessionConnectorState:
        return self.state

    async def reconfigure_connectors(
        self,
        catalog: ResolvedConnectorCatalog,
        selection: ResolvedConnectorSelection,
        *,
        force: bool,
    ) -> SessionConnectorState:
        self.attempts.append((catalog, selection, force))
        if self.busy:
            raise SessionBackendError(ProtocolErrorCode.CONFLICT, "busy")
        self.state = replace(
            self.state,
            accepted_catalog_revision=catalog.revision,
            accepted_selection_revision=selection.selection_revision,
            route_revision="routes:2",
        )
        return self.state

    async def suspend_connectors(
        self, *, name: str, tool_name: str | None, reason: str
    ) -> SessionConnectorState:
        del name, tool_name, reason
        return self.state

    async def request_connector_auth(self, *, alias: str) -> ConnectorAuthRequest:
        raise NotImplementedError(alias)

    async def request_connector_connect(self, *, alias: str) -> ConnectorConnectRequest:
        source = next(item for item in self.state.sources if item.alias == alias)
        return ConnectorConnectRequest(
            session_id=self.session_id,
            raw_connector_id=source.raw_id,
            alias=alias,
            display_name=source.display_name,
        )


@pytest.mark.asyncio
async def test_connector_cache_ttl_never_slides(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A catalog fetched at a fixed time and later service instances sharing its cache.
    *Do*: Read the cache repeatedly before and at the ten-minute boundary.
    *Assert*: Reads do not extend the original stored-at expiry.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    cache_path = tmp_path / "connectors.json"
    now = [1_000]
    fetch_count = 0

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        nonlocal fetch_count
        fetch_count += 1
        return {"connectors": [_connector()]}

    orchestrator = _orchestrator()
    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=cache_path,
        fetch_bootstrap=fetch,
        clock=lambda: now[0],
    )
    await service.resolve_catalog(orchestrator)

    # Do
    now[0] = 1_100
    first_reader = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=cache_path,
        fetch_bootstrap=fetch,
        clock=lambda: now[0],
    )
    first = first_reader.read_catalog(orchestrator)
    now[0] = 1_599
    second = first_reader.read_catalog(orchestrator)
    now[0] = 1_600
    expired = first_reader.read_catalog(orchestrator)

    # Assert
    assert first.disposition == "fresh_cache"
    assert first.catalog is not None
    assert second.disposition == "memory"
    assert expired.disposition == "not_loaded"
    assert expired.catalog is None
    assert fetch_count == 1


@pytest.mark.asyncio
async def test_opening_reads_an_expired_catalog_without_fetching(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A catalog cached an hour ago and a service that cannot fetch.
    *Do*: Read the cached catalog for opening a session, and with nothing cached.
    *Assert*: The expired catalog is returned without a fetch, and an empty
      cache answers None: opening never waits on the connector service.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    cache_path = tmp_path / "connectors.json"

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        return {"connectors": [_connector()]}

    async def unreachable(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        raise AssertionError("opening a session must not fetch")

    orchestrator = _orchestrator()
    await ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=cache_path,
        fetch_bootstrap=fetch,
        clock=lambda: 1_000,
    ).resolve_catalog(orchestrator)

    def service(path: Path) -> ConnectorCatalogService:
        return ConnectorCatalogService(
            implicit_source_enabled=False,
            cache_path=path,
            fetch_bootstrap=unreachable,
            clock=lambda: 1_000 + 3_600,
        )

    # Do
    expired = await service(cache_path).read_cached_catalog(orchestrator)
    empty = await service(tmp_path / "empty.json").read_cached_catalog(orchestrator)

    # Assert
    assert expired is not None
    assert [connector.alias for connector in expired.connectors] == ["wiki"]
    assert empty is None


@pytest.mark.asyncio
async def test_a_failed_bootstrap_answers_the_next_callers_until_it_retries(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A connector backend that fails, with no cached catalog.
    *Do*: Resolve the catalog again inside the retry window, then after it.
    *Assert*: Callers inside the window fail at once without another fetch; the
    first one after it fetches again and recovers. Each caller would otherwise
    wait out its own bootstrap timeout against a backend that just failed.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    now = [1_000]
    fetches = 0
    backend_up = False

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        nonlocal fetches
        fetches += 1
        if not backend_up:
            raise TimeoutError("connectors backend is down")
        return {"connectors": [_connector()]}

    orchestrator = _orchestrator()
    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
        clock=lambda: now[0],
    )
    with pytest.raises(ConnectorCatalogUnavailableError):
        await service.resolve_catalog(orchestrator)

    # Do
    now[0] = 1_059
    with pytest.raises(ConnectorCatalogUnavailableError):
        await service.resolve_catalog(orchestrator)
    fetches_in_window = fetches
    backend_up = True
    now[0] = 1_060
    recovered = await service.resolve_catalog(orchestrator)

    # Assert
    assert fetches_in_window == 1
    assert recovered is not None
    assert fetches == 2


@pytest.mark.parametrize(("status", "fails"), [(404, False), (503, True)])
@pytest.mark.asyncio
async def test_a_server_without_a_connector_service_has_no_connectors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: int, fails: bool
) -> None:
    """*Prepare*: A server answering the bootstrap with 404, as one without the
      connector service does (self-hosted, Private Cloud, a proxy), or 503.
    *Do*: Resolve the catalog.
    *Assert*: A 404 is an empty catalog, not a failure to warn about; a 503
      still fails.
    """
    import httpx2

    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    async def fetch(
        base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        request = httpx2.Request("GET", f"{base_url}/v1/connectors/bootstrap")
        response = httpx2.Response(status, request=request)
        raise httpx2.HTTPStatusError("bootstrap", request=request, response=response)

    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
        clock=lambda: 1_000,
    )

    # Do
    try:
        catalog = await service.resolve_catalog(_orchestrator())
        error = None
    except ConnectorCatalogUnavailableError as exc:
        catalog, error = None, exc

    # Assert
    if fails:
        assert error is not None
        assert "HTTP 503" in str(error)
    else:
        assert error is None
        assert catalog is not None
        assert catalog.connectors == ()


@pytest.mark.asyncio
async def test_a_failure_nobody_remembers_leaves_the_next_caller_to_fetch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A connector backend that fails, with no cached catalog.
    *Do*: Resolve without remembering the failure, as the app server's warm-up
      does, then resolve again at once, as a session does.
    *Assert*: The session's resolve fetches again instead of inheriting the
      warm-up's failure.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    fetches = 0

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        nonlocal fetches
        fetches += 1
        raise TimeoutError("connectors backend is down")

    orchestrator = _orchestrator()
    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
        clock=lambda: 1_000,
    )

    # Do
    with pytest.raises(ConnectorCatalogUnavailableError):
        await service.resolve_catalog(orchestrator, remember_failure=False)
    with pytest.raises(ConnectorCatalogUnavailableError):
        await service.resolve_catalog(orchestrator)

    # Assert
    assert fetches == 2


@pytest.mark.asyncio
async def test_a_failed_bootstrap_answers_another_service_in_the_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: One catalog service whose bootstrap just failed.
    *Do*: Resolve the same provider's catalog through a second service.
    *Assert*: The second fails at once without fetching. Every session in a
    shared app-server process builds its own service, so a failure kept per
    service would let each session wait out the same dead backend again.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    fetches = 0

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        nonlocal fetches
        fetches += 1
        raise TimeoutError("connectors backend is down")

    orchestrator = _orchestrator()

    def service() -> ConnectorCatalogService:
        return ConnectorCatalogService(
            implicit_source_enabled=False,
            cache_path=tmp_path / "connectors.json",
            fetch_bootstrap=fetch,
            clock=lambda: 1_000,
        )

    with pytest.raises(ConnectorCatalogUnavailableError):
        await service().resolve_catalog(orchestrator)

    # Do
    with pytest.raises(ConnectorCatalogUnavailableError):
        await service().resolve_catalog(orchestrator)

    # Assert
    assert fetches == 1


@pytest.mark.asyncio
async def test_concurrent_first_fetches_share_one_bootstrap_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: Two catalog services, no cached catalog, and a slow backend.
    *Do*: Resolve the same provider's catalog through both at once.
    *Assert*: One request answers both. Two sessions opening together in a
    shared process would otherwise each wait out the same slow request.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    fetches = 0
    release = asyncio.Event()

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        nonlocal fetches
        fetches += 1
        await release.wait()
        return {"connectors": [_connector()]}

    orchestrator = _orchestrator()

    def service(name: str) -> ConnectorCatalogService:
        return ConnectorCatalogService(
            implicit_source_enabled=False,
            cache_path=tmp_path / f"{name}.json",
            fetch_bootstrap=fetch,
            clock=lambda: 1_000,
        )

    # Do
    first = asyncio.create_task(service("first").resolve_catalog(orchestrator))
    second = asyncio.create_task(service("second").resolve_catalog(orchestrator))
    await asyncio.sleep(0)
    release.set()
    catalogs = await asyncio.gather(first, second)

    # Assert
    assert fetches == 1
    assert all(catalog is not None for catalog in catalogs)


@pytest.mark.asyncio
async def test_a_shared_bootstrap_outlives_one_waiter_but_not_all_of_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: Two services waiting on one slow first fetch.
    *Do*: Cancel one waiter and let the fetch finish, then start another fetch,
      cancel its only waiter, and resolve again while that one is cancelling.
    *Assert*: The other waiter still gets the catalog, a fetch nobody waits on
      any more is cancelled rather than left running past its session, and the
      caller arriving meanwhile gets a fetch of its own, not the cancellation.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    release = asyncio.Event()
    fetch_cancelled = asyncio.Event()

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        try:
            await release.wait()
        except asyncio.CancelledError:
            fetch_cancelled.set()
            raise
        return {"connectors": [_connector()]}

    orchestrator = _orchestrator()

    def service(name: str) -> ConnectorCatalogService:
        return ConnectorCatalogService(
            implicit_source_enabled=False,
            cache_path=tmp_path / f"{name}.json",
            fetch_bootstrap=fetch,
            clock=lambda: 1_000,
        )

    async def join(name: str, waiters: int) -> asyncio.Task[object]:
        task = asyncio.create_task(service(name).resolve_catalog(orchestrator))
        while (
            sum(f.waiters for f in connector_catalog._BOOTSTRAP_IN_FLIGHT.values())
            < waiters
        ):
            await asyncio.sleep(0)
        return task

    # Do
    leaving = await join("leaving", 1)
    staying = await join("staying", 2)
    leaving.cancel()
    release.set()
    catalog = await staying
    release.clear()
    abandoned = await join("abandoned", 1)
    abandoned.cancel()
    await asyncio.sleep(0)
    arriving = asyncio.create_task(service("arriving").resolve_catalog(orchestrator))
    await asyncio.wait_for(fetch_cancelled.wait(), timeout=1)
    release.set()
    arrived = await asyncio.wait_for(arriving, timeout=1)

    # Assert
    assert catalog is not None
    assert leaving.cancelled()
    assert abandoned.cancelled()
    assert arrived is not None


@pytest.mark.asyncio
async def test_a_forced_refresh_retries_a_failed_bootstrap_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A bootstrap that just failed and a backend that is back.
    *Do*: Force a refresh inside the retry window.
    *Assert*: It fetches and recovers. A user asking to refresh connectors
    must not be answered from the remembered failure.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    backend_up = False

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        if not backend_up:
            raise TimeoutError("connectors backend is down")
        return {"connectors": [_connector()]}

    orchestrator = _orchestrator()
    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
        clock=lambda: 1_000,
    )
    with pytest.raises(ConnectorCatalogUnavailableError):
        await service.resolve_catalog(orchestrator)
    backend_up = True

    # Do
    refreshed = await service.resolve_catalog(orchestrator, force_refresh=True)

    # Assert
    assert refreshed is not None


def test_connector_cache_reads_safe_legacy_record_without_rewrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A fresh unversioned cache record written by the legacy registry.
    *Do*: Hydrate it through the host-owned catalog reader.
    *Assert*: The record is accepted, bounded in memory, and left byte-for-byte untouched.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    # Default config keeps the experimental document_library connector off, so
    # the resolved builtins — and thus the cache key — are web_search only.
    fingerprint = connector_cache_fingerprint(
        "test-key", "https://api.mistral.ai", ("web_search",)
    )
    cache_path = tmp_path / "connectors.json"
    cache_path.write_text(
        json.dumps({
            fingerprint: {
                "stored_at_timestamp": 1_000,
                "payload": {
                    "connectors": [
                        _connector(
                            ready=False,
                            bootstrap_errors=["private upstream body with a token"],
                        )
                    ]
                },
            }
        }),
        encoding="utf-8",
    )
    before = cache_path.read_bytes()
    service = ConnectorCatalogService(
        implicit_source_enabled=False, cache_path=cache_path, clock=lambda: 1_100
    )

    # Do
    result = service.read_catalog(_orchestrator())

    # Assert
    assert result.disposition == "fresh_cache"
    assert result.catalog is not None
    assert result.catalog.connectors[0].diagnostics == (
        "Connector failed to bootstrap.",
    )
    assert cache_path.read_bytes() == before


@pytest.mark.asyncio
async def test_connector_cache_round_trip_preserves_diagnostics_and_revision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    cache_path = tmp_path / "connectors.json"

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        return {
            "connectors": [
                _connector(
                    ready=False,
                    bootstrap_errors=["oauth_failed: private provider detail"],
                )
            ]
        }

    writer = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=cache_path,
        fetch_bootstrap=fetch,
        clock=lambda: 1_000,
    )
    live = await writer.resolve_catalog(_orchestrator())
    reader = ConnectorCatalogService(
        implicit_source_enabled=False, cache_path=cache_path, clock=lambda: 1_001
    )

    cached = reader.read_catalog(_orchestrator()).catalog

    assert live is not None
    assert cached is not None
    assert cached.revision == live.revision
    assert (
        cached.connectors[0].diagnostics
        == live.connectors[0].diagnostics
        == ("Connector bootstrap issue: oauth_failed",)
    )


def test_concurrent_connector_cache_writers_preserve_account_entries(
    tmp_path: Path,
) -> None:
    first_read = Event()
    release_first = Event()
    second_done = Event()
    cache_path = tmp_path / "connectors.json"

    class PausingCache(ConnectorCatalogCache):
        def _read_entries(self) -> dict[str, object]:
            entries = super()._read_entries()
            first_read.set()
            assert release_first.wait(timeout=2)
            return entries

    def write_second() -> None:
        ConnectorCatalogCache(cache_path).write(
            _cache_catalog("second", "second"), stored_at=1_000
        )
        second_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(
            PausingCache(cache_path).write,
            _cache_catalog("first", "first"),
            stored_at=1_000,
        )
        assert first_read.wait(timeout=2)
        second = executor.submit(write_second)
        assert not second_done.wait(timeout=0.1)
        release_first.set()
        first.result(timeout=2)
        second.result(timeout=2)

    assert set(json.loads(cache_path.read_text(encoding="utf-8"))) == {
        "first",
        "second",
    }


@pytest.mark.parametrize(
    "entry",
    [
        _v2_entry({"connectors": []}, stored_at=1_101),
        _v2_entry({"connectors": "not-a-list"}),
        _v2_entry({"connectors": [_connector()]}, stored_at=500),
    ],
    ids=["future", "malformed", "expired"],
)
def test_connector_cache_rejects_unsafe_records(
    tmp_path: Path, entry: dict[str, object]
) -> None:
    """*Prepare*: A future, malformed, expired, or over-limit V2 cache record.
    *Do*: Read the record without network fallback.
    *Assert*: The unsafe record is treated as a cache miss.
    """
    # Prepare
    fingerprint = "f" * 64
    cache_path = tmp_path / "connectors.json"
    cache_path.write_text(json.dumps({fingerprint: entry}), encoding="utf-8")

    # Do
    result = ConnectorCatalogCache(cache_path).read(fingerprint, now=1_100)

    # Assert
    assert result is None


def test_connector_cache_drops_faulty_connector_but_keeps_siblings(
    tmp_path: Path,
) -> None:
    # A cached record with one invalid connector must not poison the whole cache
    # read: the faulty row is dropped and its healthy sibling survives.
    entry = _v2_entry({
        "connectors": [
            _connector(name="wiki"),
            {
                **_connector(connector_id="c-dup", name="dup"),
                "tools": [
                    {"name": "same", "inputSchema": {}},
                    {"name": "same", "inputSchema": {}},
                ],
            },
        ]
    })
    fingerprint = "f" * 64
    cache_path = tmp_path / "connectors.json"
    cache_path.write_text(json.dumps({fingerprint: entry}), encoding="utf-8")

    result = ConnectorCatalogCache(cache_path).read(fingerprint, now=1_100)

    assert result is not None
    assert [connector.alias for connector in result.catalog.connectors] == ["wiki"]


@pytest.mark.asyncio
async def test_successful_bootstrap_writes_redacted_bounded_v2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A bootstrap response containing service-only fields and a raw diagnostic.
    *Do*: Resolve the account catalog through the service.
    *Assert*: The atomic V2 cache contains only reduced fields and no credentials or raw body.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "super-secret-key")
    cache_path = tmp_path / "connectors.json"

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        return {
            "connectors": [
                {
                    **_connector(
                        bootstrap_errors=["Bearer super-secret-token from upstream"]
                    ),
                    "description": "private connector description",
                    "auth_action": {
                        "type": "oauth",
                        "url": "https://private.example.com/auth",
                    },
                    "tools": [
                        {
                            "name": "search",
                            "description": "Search docs",
                            "inputSchema": {"type": "object"},
                            "secret_extra": "tool-secret",
                        }
                    ],
                }
            ]
        }

    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=cache_path,
        fetch_bootstrap=fetch,
        clock=lambda: 1_000,
    )

    # Do
    catalog = await service.resolve_catalog(_orchestrator())

    # Assert
    assert catalog is not None
    cache_text = cache_path.read_text(encoding="utf-8")
    assert "super-secret" not in cache_text
    assert "private connector description" not in cache_text
    assert "private.example.com" not in cache_text
    assert "tool-secret" not in cache_text
    entry = next(iter(json.loads(cache_text).values()))
    assert entry["format"] == 2
    assert entry["stored_at"] == 1_000
    connector = entry["payload"]["connectors"][0]
    assert set(connector) == {
        "auth_action",
        "diagnostics",
        "display_name",
        "id",
        "name",
        "protocol",
        "status",
        "tools",
    }


@pytest.mark.asyncio
async def test_failed_forced_refresh_preserves_last_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A live in-memory catalog and a later failing full bootstrap.
    *Do*: Force refresh and then read the service again.
    *Assert*: Failure changes neither the accepted host catalog nor the cache file.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    cache_path = tmp_path / "connectors.json"
    should_fail = False

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        if should_fail:
            raise RuntimeError("raw private provider response")
        return {"connectors": [_connector()]}

    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=cache_path,
        fetch_bootstrap=fetch,
        clock=lambda: 1_000,
    )
    orchestrator = _orchestrator()
    initial = await service.resolve_catalog(orchestrator)
    before = cache_path.read_bytes()
    should_fail = True

    # Do
    with pytest.raises(ConnectorCatalogUnavailableError) as exc_info:
        await service.resolve_catalog(orchestrator, force_refresh=True)
    retained = service.read_catalog(orchestrator)

    # Assert
    assert "raw private provider response" not in str(exc_info.value)
    assert retained.catalog == initial
    assert retained.disposition == "memory"
    assert cache_path.read_bytes() == before


def test_connector_aliases_are_collision_safe_and_missing_ids_are_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A cache with colliding display names and one connector without an ID.
    *Do*: Resolve it into the immutable host catalog.
    *Assert*: Aliases are deterministic and only connectors with IDs enter the catalog.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    # Default config keeps the experimental document_library connector off, so
    # the resolved builtins — and thus the cache key — are web_search only.
    fingerprint = connector_cache_fingerprint(
        "test-key", "https://api.mistral.ai", ("web_search",)
    )
    cache_path = tmp_path / "connectors.json"
    cache_path.write_text(
        json.dumps({
            fingerprint: {
                "stored_at_timestamp": 1_000,
                "payload": {
                    "connectors": [
                        _connector(connector_id="one", name="Docs & Search"),
                        _connector(connector_id="two", name="Docs & Search"),
                        {**_connector(connector_id="three"), "id": None},
                    ]
                },
            }
        }),
        encoding="utf-8",
    )
    service = ConnectorCatalogService(
        implicit_source_enabled=False, cache_path=cache_path, clock=lambda: 1_100
    )

    # Do
    result = service.read_catalog(_orchestrator())

    # Assert
    assert result.catalog is not None
    assert [(item.raw_id, item.alias) for item in result.catalog.connectors] == [
        ("one", "Docs___Search"),
        ("two", "Docs___Search_2"),
    ]


@pytest.mark.asyncio
async def test_connector_aliases_and_revision_are_independent_of_payload_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    first = _connector(connector_id="one", name="Docs & Search")
    second = _connector(connector_id="two", name="Docs & Search")
    payloads = iter(({"connectors": [second, first]}, {"connectors": [first, second]}))

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        return next(payloads)

    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
    )
    initial = await service.resolve_catalog(_orchestrator())
    reordered = await service.resolve_catalog(_orchestrator(), force_refresh=True)

    assert initial is not None
    assert reordered is not None
    assert reordered.revision == initial.revision
    assert (
        [(item.raw_id, item.alias) for item in reordered.connectors]
        == [(item.raw_id, item.alias) for item in initial.connectors]
        == [("one", "Docs___Search"), ("two", "Docs___Search_2")]
    )


@pytest.mark.asyncio
async def test_connector_collision_suffix_stays_within_public_name_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    long_name = "x" * 256

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        return {
            "connectors": [
                _connector(connector_id="one", name=long_name),
                _connector(connector_id="two", name=long_name),
            ]
        }

    catalog = await ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
    ).resolve_catalog(_orchestrator())

    assert catalog is not None
    aliases = [connector.alias for connector in catalog.connectors]
    assert aliases[0] == long_name
    assert aliases[1].endswith("_2")
    assert len(aliases[1]) == 256


@pytest.mark.parametrize("names", [("search", "search"), ("search", " search ")])
def test_resolve_drops_connector_with_duplicate_trimmed_tool_names(
    names: tuple[str, str],
) -> None:
    faulty = _connector(connector_id="c-dup", name="dup")
    faulty["tools"] = [
        {"name": name, "description": name, "inputSchema": {}} for name in names
    ]

    catalog = connector_catalog._resolve_catalog(
        {"connectors": [_connector(name="wiki"), faulty]}, "fingerprint"
    )

    # The faulty connector is dropped; its healthy sibling still loads.
    assert [connector.alias for connector in catalog.connectors] == ["wiki"]


def test_resolve_drops_connector_exceeding_tool_limit() -> None:
    over_limit = _connector(connector_id="c-many", name="many")
    over_limit["tools"] = [
        {"name": f"tool_{index}", "description": "x", "inputSchema": {}}
        for index in range(connector_catalog._MAX_TOOLS_PER_CONNECTOR + 1)
    ]

    catalog = connector_catalog._resolve_catalog(
        {"connectors": [over_limit, _connector(name="wiki")]}, "fingerprint"
    )

    assert [connector.alias for connector in catalog.connectors] == ["wiki"]


def test_resolve_drops_oversized_tool_but_keeps_connector() -> None:
    connector = _connector(connector_id="c-big", name="big")
    connector["tools"] = [
        {"name": "ok", "description": "x", "inputSchema": {}},
        {
            "name": "huge",
            "description": "x",
            # Padding beyond the 64 KiB cap; only this tool is dropped.
            "inputSchema": {"type": "object", "pad": "x" * (65 * 1024)},
        },
    ]

    catalog = connector_catalog._resolve_catalog(
        {"connectors": [connector]}, "fingerprint"
    )

    # The connector survives with its healthy tool; the oversized one is gone.
    assert len(catalog.connectors) == 1
    assert [tool.raw_name for tool in catalog.connectors[0].tools] == ["ok"]


def test_resolve_drops_unnamed_tool_but_keeps_connector() -> None:
    connector = _connector(connector_id="c-x", name="x")
    connector["tools"] = [
        {"name": "ok", "description": "x", "inputSchema": {}},
        {"name": "   ", "description": "blank", "inputSchema": {}},
    ]

    catalog = connector_catalog._resolve_catalog(
        {"connectors": [connector]}, "fingerprint"
    )

    assert len(catalog.connectors) == 1
    assert [tool.raw_name for tool in catalog.connectors[0].tools] == ["ok"]


def test_resolve_drops_all_faulty_connectors_yielding_empty_catalog() -> None:
    faulty = _connector(connector_id="c-bad", name="bad")
    faulty["tools"] = [{"name": "n", "description": "n", "inputSchema": {}}] * 2

    catalog = connector_catalog._resolve_catalog(
        {"connectors": [faulty]}, "fingerprint"
    )

    assert catalog.connectors == ()


def test_resolve_tail_truncates_over_cap_catalog() -> None:
    over_cap = connector_catalog._MAX_CONNECTORS + 5
    payload = {
        "connectors": [
            _connector(connector_id=f"c-{index:04d}", name=f"conn{index:04d}")
            for index in range(over_cap)
        ]
    }

    catalog = connector_catalog._resolve_catalog(payload, "fingerprint")

    # The tail beyond the cap is dropped after the deterministic id sort, so the
    # catalog keeps the first _MAX_CONNECTORS instead of being rejected wholesale.
    assert len(catalog.connectors) == connector_catalog._MAX_CONNECTORS
    assert catalog.connectors[0].raw_id == "c-0000"
    assert (
        catalog.connectors[-1].raw_id
        == f"c-{connector_catalog._MAX_CONNECTORS - 1:04d}"
    )


def test_resolve_drops_duplicate_id_connectors_but_keeps_unique_siblings() -> None:
    # A duplicated id has an ambiguous identity, so every copy is dropped while
    # uniquely identified siblings still load.
    payload = {
        "connectors": [
            _connector(connector_id="same", name="a"),
            _connector(connector_id="same", name="b"),
            _connector(connector_id="unique", name="c"),
        ]
    }

    catalog = connector_catalog._resolve_catalog(payload, "fingerprint")

    assert [connector.raw_id for connector in catalog.connectors] == ["unique"]


def test_resolve_still_aborts_on_broken_envelope() -> None:
    # A connectors field that is not a list is whole-payload corruption, so
    # resolution stays fatal rather than guessing at partial recovery.
    with pytest.raises(ConnectorCatalogValidationError, match="malformed"):
        connector_catalog._resolve_catalog({"connectors": "nope"}, "fingerprint")


def test_resolve_drops_malformed_connector_but_keeps_siblings() -> None:
    # A connector whose wire shape is invalid (tools is not a list) is dropped
    # without taking its healthy sibling down.
    malformed = {"id": "c-bad", "name": "bad", "tools": "not-a-list"}

    catalog = connector_catalog._resolve_catalog(
        {"connectors": [malformed, _connector(name="wiki")]}, "fingerprint"
    )

    assert [connector.alias for connector in catalog.connectors] == ["wiki"]


def test_resolve_drops_malformed_tool_but_keeps_connector() -> None:
    # A tool missing its required name key is dropped on its own; the connector
    # keeps its healthy tool instead of the whole payload failing to parse.
    connector = _connector(connector_id="c-x", name="x")
    connector["tools"] = [
        {"name": "ok", "description": "x", "inputSchema": {}},
        {"description": "no name key", "inputSchema": {}},
    ]

    catalog = connector_catalog._resolve_catalog(
        {"connectors": [connector]}, "fingerprint"
    )

    assert len(catalog.connectors) == 1
    assert [tool.raw_name for tool in catalog.connectors[0].tools] == ["ok"]


def test_resolve_keeps_alias_stable_when_colliding_sibling_dropped() -> None:
    # `a-id` sorts before `b-id` and shares the normalized alias; dropping it for
    # bad tools must not promote the survivor from `dup_2` to `dup`, which would
    # break any selection persisted against `dup_2`.
    dropped = _connector(connector_id="a-id", name="dup")
    dropped["tools"] = [
        {"name": "same", "description": "x", "inputSchema": {}},
        {"name": "same", "description": "x", "inputSchema": {}},
    ]
    survivor = _connector(connector_id="b-id", name="dup")

    catalog = connector_catalog._resolve_catalog(
        {"connectors": [dropped, survivor]}, "fingerprint"
    )

    assert [(c.raw_id, c.alias) for c in catalog.connectors] == [("b-id", "dup_2")]


def test_resolve_truncation_keeps_healthy_tail_over_invalid_head() -> None:
    # Fill every cap slot with invalid connectors that sort first, then a single
    # healthy connector in the tail. Filtering before truncating keeps the
    # healthy one instead of yielding an empty catalog.
    max_connectors = connector_catalog._MAX_CONNECTORS

    def faulty(index: int) -> dict[str, object]:
        connector = _connector(connector_id=f"c-{index:04d}", name=f"bad{index}")
        connector["tools"] = [
            {"name": "same", "description": "x", "inputSchema": {}},
            {"name": "same", "description": "x", "inputSchema": {}},
        ]
        return connector

    payload = {
        "connectors": [faulty(index) for index in range(max_connectors)]
        + [_connector(connector_id=f"c-{max_connectors:04d}", name="healthy")]
    }

    catalog = connector_catalog._resolve_catalog(payload, "fingerprint")

    assert [connector.raw_id for connector in catalog.connectors] == [
        f"c-{max_connectors:04d}"
    ]


def test_resolve_aggregates_dropped_tool_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Many unusable tools on one connector emit a single summary warning, not one
    # line per tool, to bound the log volume on a bad refresh.
    connector = _connector(connector_id="c", name="c")
    connector["tools"] = [
        {"name": "   ", "description": "blank", "inputSchema": {}} for _ in range(5)
    ]

    with caplog.at_level(logging.WARNING, logger="vibe"):
        connector_catalog._resolve_catalog({"connectors": [connector]}, "fingerprint")

    tool_drop_logs = [
        record
        for record in caplog.records
        if getattr(record, "connector_drop_reason", None) == "tools_dropped"
    ]
    assert len(tool_drop_logs) == 1
    assert getattr(tool_drop_logs[0], "connector_tool_drop_counts", None) == {
        "unnamed_tool": 5
    }


def test_connector_selection_preserves_explicit_policy_precedence() -> None:
    """*Prepare*: Legacy and Unified defaults plus explicit source, tool, allow, and deny rules.
    *Do*: Resolve effective connector and tool enablement.
    *Assert*: Only absence uses the process default; every explicit restriction still wins.
    """
    # Prepare
    base = build_test_vibe_config(enable_connectors=True)
    catalog = _selection_catalog()
    legacy = resolve_connector_selection(base, catalog, implicit_source_enabled=False)
    unified = resolve_connector_selection(base, catalog, implicit_source_enabled=True)
    explicit = resolve_connector_selection(
        build_test_vibe_config(
            enable_connectors=True,
            connectors=[
                ConnectorConfig(
                    name="github", disabled=False, disabled_tools=["write"]
                ),
                ConnectorConfig(name="linear", disabled=True),
            ],
            enabled_tools=["connector_github_*"],
            disabled_tools=["connector_github_delete"],
        ),
        catalog,
        implicit_source_enabled=True,
    )

    # Do
    outcomes = {
        "legacy_absent": connector_source_enabled(legacy, "github"),
        "unified_absent": connector_source_enabled(unified, "github"),
        "explicit_opt_in": connector_source_enabled(explicit, "github"),
        "explicit_opt_out": connector_source_enabled(explicit, "linear"),
        "source_tool": connector_tool_enabled(
            explicit, alias="github", raw_tool_name="write"
        ),
        "allowlist": connector_tool_enabled(
            explicit, alias="slack", raw_tool_name="search"
        ),
        "denylist": connector_tool_enabled(
            explicit, alias="github", raw_tool_name="delete"
        ),
        "allowed": connector_tool_enabled(
            explicit, alias="github", raw_tool_name="search"
        ),
    }

    # Assert
    assert outcomes == {
        "legacy_absent": False,
        "unified_absent": True,
        "explicit_opt_in": True,
        "explicit_opt_out": False,
        "source_tool": False,
        "allowlist": False,
        "denylist": False,
        "allowed": True,
    }


def test_selection_revision_ignores_ineffective_configuration_and_catalog_revision() -> (
    None
):
    catalog = _selection_catalog()
    base = resolve_connector_selection(
        build_test_vibe_config(enable_connectors=True),
        catalog,
        implicit_source_enabled=False,
    )
    pending_alias = resolve_connector_selection(
        build_test_vibe_config(
            enable_connectors=True,
            connectors=[ConnectorConfig(name="undiscovered", disabled=False)],
        ),
        catalog,
        implicit_source_enabled=False,
    )
    unknown_tool = resolve_connector_selection(
        build_test_vibe_config(
            enable_connectors=True,
            connectors=[
                ConnectorConfig(
                    name="github", disabled=True, disabled_tools=["not_in_catalog"]
                )
            ],
        ),
        catalog,
        implicit_source_enabled=False,
    )
    catalog_only = resolve_connector_selection(
        build_test_vibe_config(enable_connectors=True),
        replace(catalog, revision="different-catalog-revision"),
        implicit_source_enabled=False,
    )

    assert pending_alias.selection_revision == base.selection_revision
    assert unknown_tool.selection_revision == base.selection_revision
    assert catalog_only.selection_revision == base.selection_revision


def test_selection_revision_changes_for_effective_source_and_tool_decisions() -> None:
    catalog = _selection_catalog()
    legacy_default = resolve_connector_selection(
        build_test_vibe_config(enable_connectors=True),
        catalog,
        implicit_source_enabled=False,
    )
    source_enabled = resolve_connector_selection(
        build_test_vibe_config(
            enable_connectors=True,
            connectors=[ConnectorConfig(name="github", disabled=False)],
        ),
        catalog,
        implicit_source_enabled=False,
    )
    tool_disabled = resolve_connector_selection(
        build_test_vibe_config(
            enable_connectors=True,
            connectors=[
                ConnectorConfig(
                    name="github", disabled=False, disabled_tools=["search"]
                )
            ],
        ),
        catalog,
        implicit_source_enabled=False,
    )

    assert source_enabled.selection_revision != legacy_default.selection_revision
    assert tool_disabled.selection_revision != source_enabled.selection_revision


@pytest.mark.asyncio
async def test_busy_convergence_retains_candidate_across_later_config_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    fetch_count = 0

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        nonlocal fetch_count
        fetch_count += 1
        return {"connectors": [_connector()]}

    orchestrator = _orchestrator()
    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
    )
    catalog = await service.resolve_catalog(orchestrator)
    assert catalog is not None
    initial_selection = service.resolve_selection(orchestrator, catalog)
    root = _BusyConnectorControl(
        orchestrator,
        SessionConnectorState(
            accepted_catalog_revision=catalog.revision,
            accepted_selection_revision=initial_selection.selection_revision,
            route_revision="routes:1",
            sources=(
                SessionConnectorSourceState(
                    raw_id="connector-1",
                    alias="wiki",
                    display_name="wiki",
                    status="disabled",
                    tools=(
                        SessionConnectorToolDescriptor(
                            raw_name="search",
                            description="Search docs",
                            enabled=False,
                            display_name="connector_wiki_search",
                        ),
                    ),
                ),
            ),
            discovery_errors={},
        ),
    )

    async def notify(*_args: object) -> None:
        return None

    with pytest.raises(SessionBackendError) as exc_info:
        await service.dispatch(
            "connector_catalog/toggle",
            {"sessionId": root.session_id, "alias": "wiki", "disabled": False},
            root=cast(SessionBackend, root),
            notify=notify,
        )
    assert exc_info.value.code is ProtocolErrorCode.CONFLICT
    assert len(root.attempts) == 1
    queued_catalog, queued_selection, queued_force = root.attempts[0]

    await orchestrator.set_field("/connectors", [{"name": "wiki", "disabled": True}])
    root.busy = False
    assert (
        await service.converge_pending_connector_candidate(
            root.session_id, cast(SessionBackend, root)
        )
        is None
    )

    assert len(root.attempts) == 2
    retried_catalog, retried_selection, retried_force = root.attempts[1]
    assert retried_catalog is queued_catalog
    assert retried_selection is queued_selection
    assert retried_force is queued_force is False
    assert orchestrator.config.connectors[0].disabled is True
    assert retried_selection.connector_settings[0].disabled is False
    assert root.state.accepted_selection_revision == queued_selection.selection_revision
    assert fetch_count == 1


@pytest.mark.asyncio
async def test_successful_accept_discards_stale_pending_candidate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        return {"connectors": [_connector()]}

    orchestrator = _orchestrator()
    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
    )
    catalog = await service.resolve_catalog(orchestrator)
    assert catalog is not None
    initial_selection = service.resolve_selection(orchestrator, catalog)
    root = _BusyConnectorControl(
        orchestrator,
        SessionConnectorState(
            accepted_catalog_revision=catalog.revision,
            accepted_selection_revision=initial_selection.selection_revision,
            route_revision="routes:1",
            sources=(
                SessionConnectorSourceState(
                    raw_id="connector-1",
                    alias="wiki",
                    display_name="wiki",
                    status="disabled",
                    tools=(
                        SessionConnectorToolDescriptor(
                            raw_name="search",
                            description="Search docs",
                            enabled=False,
                            display_name="connector_wiki_search",
                        ),
                    ),
                ),
            ),
            discovery_errors={},
        ),
    )

    async def notify(*_args: object) -> None:
        return None

    with pytest.raises(SessionBackendError):
        await service.dispatch(
            "connector_catalog/toggle",
            {"sessionId": root.session_id, "alias": "wiki", "disabled": False},
            root=cast(SessionBackend, root),
            notify=notify,
        )

    root.busy = False
    await service.dispatch(
        "connector_catalog/toggle",
        {"sessionId": root.session_id, "alias": "wiki", "disabled": True},
        root=cast(SessionBackend, root),
        notify=notify,
    )
    accepted_selection = root.attempts[1][1]

    assert (
        await service.converge_pending_connector_candidate(
            root.session_id, cast(SessionBackend, root)
        )
        is None
    )
    assert len(root.attempts) == 2
    assert root.state.accepted_selection_revision == (
        accepted_selection.selection_revision
    )


@pytest.mark.asyncio
async def test_toggle_applies_to_the_accepted_catalog_after_the_cache_expires(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    now = [1_000]

    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        return {"connectors": [_connector()]}

    orchestrator = _orchestrator()
    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
        clock=lambda: now[0],
    )
    catalog = await service.resolve_catalog(orchestrator)
    assert catalog is not None
    root = _BusyConnectorControl(
        orchestrator,
        SessionConnectorState(
            accepted_catalog_revision=catalog.revision,
            accepted_selection_revision=service.resolve_selection(
                orchestrator, catalog
            ).selection_revision,
            route_revision="routes:1",
            sources=(
                SessionConnectorSourceState(
                    raw_id="connector-1",
                    alias="wiki",
                    display_name="wiki",
                    status="connected",
                ),
            ),
            discovery_errors={},
        ),
    )
    root.busy = False
    now[0] = 1_000 + 11 * 60

    async def notify(*_args: object) -> None:
        return None

    await service.dispatch(
        "connector_catalog/toggle",
        {"sessionId": root.session_id, "alias": "wiki", "disabled": True},
        root=cast(SessionBackend, root),
        notify=notify,
    )

    assert [attempt[0] for attempt in root.attempts] == [catalog]
    assert orchestrator.config.connectors[0].disabled is True


@pytest.mark.asyncio
async def test_an_auth_event_for_another_session_is_dropped_not_raised() -> None:
    """*Prepare*: A root session and an auth event a subagent session raised.
    *Do*: Accept the event.
    *Assert*: It is dropped rather than failing the event loop that forwards it.
    """
    # Prepare
    service = ConnectorCatalogService(implicit_source_enabled=False)
    root = MagicMock(spec=SessionBackend)
    root.session_id = "root-session"

    # Do
    accepted = await service.accept_auth_required(
        ConnectorAuthRequiredParams(
            session_id="child-session",
            alias="github",
            accepted_catalog_revision="catalog-1",
            reason="gateway_rejected",
        ),
        raw_connector_id="github-id",
        action="oauth",
        root=root,
        notify=AsyncMock(),
    )

    # Assert
    assert accepted is None


def test_resolve_marks_no_auth_connector_as_needing_a_connection() -> None:
    """*Prepare*: Bootstrap reports an unready connector with a no-auth connect action.
    *Do*: Resolve and project the catalog.
    *Assert*: It needs connecting rather than reading as unavailable.
    """
    raw = {**_connector(ready=False), "auth_action": {"type": "no_auth_connect"}}

    catalog = connector_catalog._resolve_catalog({"connectors": [raw]}, "fingerprint")

    connector = catalog.connectors[0]
    assert connector.auth_action == "no_auth_connect"
    assert connector_catalog._connector_readiness(connector) == "needs_connect"


async def _connect_fixture(
    tmp_path: Path, connected: list[bool]
) -> tuple[ConnectorCatalogService, _BusyConnectorControl]:
    async def fetch(
        _base_url: str, _api_key: str, _builtins: tuple[str, ...] = ()
    ) -> object:
        if connected[0]:
            return {"connectors": [_connector()]}
        no_auth = {
            **_connector(ready=False),
            "auth_action": {"type": "no_auth_connect"},
        }
        return {"connectors": [no_auth]}

    orchestrator = _orchestrator()
    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
    )
    catalog = await service.resolve_catalog(orchestrator)
    assert catalog is not None
    root = _BusyConnectorControl(
        orchestrator,
        SessionConnectorState(
            accepted_catalog_revision=catalog.revision,
            accepted_selection_revision="selection:1",
            route_revision="routes:1",
            sources=(
                SessionConnectorSourceState(
                    raw_id="connector-1",
                    alias="wiki",
                    display_name="Wiki",
                    status="needs_connect",
                ),
            ),
            discovery_errors={},
        ),
    )
    root.busy = False
    return service, root


async def _dispatch_connect(
    service: ConnectorCatalogService, root: _BusyConnectorControl
) -> DispatchResult:
    async def notify(*_args: object) -> None:
        return None

    return await service.dispatch(
        "connector_catalog/connect",
        {"sessionId": root.session_id, "alias": "wiki"},
        root=cast(SessionBackend, root),
        notify=notify,
    )


@pytest.mark.asyncio
async def test_connect_creates_the_connection_then_refreshes_the_catalog(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A session whose no-auth connector needs connecting.
    *Do*: Dispatch `connector_catalog/connect` for it.
    *Assert*: The connection is created for its raw id, then a forced refresh accepts the ready catalog.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    connected = [False]
    service, root = await _connect_fixture(tmp_path, connected)
    created: list[str] = []

    async def connect(_provider: object, raw_id: str) -> None:
        created.append(raw_id)
        connected[0] = True

    monkeypatch.setattr(connector_catalog, "_connect_without_credentials", connect)

    # Do
    result = await _dispatch_connect(service, root)

    # Assert
    assert created == ["connector-1"]
    assert result.runtime_updated is True
    accepted, _selection, force = root.attempts[-1]
    assert force is True
    assert [connector.ready for connector in accepted.connectors] == [True]


@pytest.mark.asyncio
async def test_connect_failure_names_the_connector_and_skips_the_refresh(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: Creating the connection fails.
    *Do*: Dispatch `connector_catalog/connect`.
    *Assert*: The error names the connector and no refresh is attempted.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    service, root = await _connect_fixture(tmp_path, [False])
    monkeypatch.setattr(
        connector_catalog,
        "_connect_without_credentials",
        AsyncMock(side_effect=RuntimeError("boom")),
    )

    # Do
    with pytest.raises(RequestFailure) as failure:
        await _dispatch_connect(service, root)

    # Assert
    assert failure.value.code is ProtocolErrorCode.INTERNAL_ERROR
    assert str(failure.value) == "Could not connect Wiki: RuntimeError."
    assert root.attempts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "message"),
    [
        (
            403,
            "Could not connect Wiki: your API key may not create connections "
            "(HTTP 403). Check your MISTRAL_API_KEY.",
        ),
        (404, "Could not connect Wiki: the connector no longer exists (HTTP 404)."),
        (
            503,
            "Could not connect Wiki (HTTP 503). Try again, "
            "or enable it in the Mistral dashboard.",
        ),
    ],
)
@respx.mock
async def test_connect_http_failure_reads_as_a_sentence_without_the_body(
    status: int, message: str
) -> None:
    """*Prepare*: The credentials endpoint rejects the request with a JSON body.
    *Do*: Connect, then format the failure.
    *Assert*: The message explains the status and never echoes the response body.
    """
    # Prepare
    respx.patch(
        "https://api.example.test/v1/connectors/connector-1/user/credentials"
    ).respond(status, json={"detail": "internal trace id abc123"})
    provider = connector_catalog._ConnectorProvider(
        fingerprint="fingerprint",
        base_url="https://api.example.test",
        api_key="test-key",
        builtins=(),
    )

    # Do
    with pytest.raises(Exception) as failure:
        await connector_catalog._connect_without_credentials(provider, "connector-1")
    formatted = connector_catalog._connect_failure_message(failure.value, "Wiki")

    # Assert
    assert formatted == message


@pytest.mark.asyncio
@respx.mock
async def test_connect_without_credentials_creates_the_default_user_connection() -> (
    None
):
    """*Prepare*: The connector credentials endpoint accepts the request.
    *Do*: Connect a no-auth connector.
    *Assert*: It posts an empty default user connection, as the dashboard does.
    """
    # Prepare
    route = respx.patch(
        "https://api.example.test/v1/connectors/connector-1/user/credentials"
    ).respond(json={"message": "ok"})
    provider = connector_catalog._ConnectorProvider(
        fingerprint="fingerprint",
        base_url="https://api.example.test",
        api_key="test-key",
        builtins=(),
    )

    # Do
    await connector_catalog._connect_without_credentials(provider, "connector-1")

    # Assert
    assert json.loads(route.calls.last.request.content) == {
        "name": "default",
        "is_default": True,
        "credentials": {"headers": {}},
    }
