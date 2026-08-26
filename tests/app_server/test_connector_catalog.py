from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
from threading import Event
from typing import cast

import pytest

from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._session_backend_port import (
    ConnectorAuthRequest,
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
from vibe.app_server.protocol import ProtocolErrorCode
from vibe.core.config import ConnectorConfig, VibeConfigSchema


def _connector(
    *,
    connector_id: str = "connector-1",
    name: str = "wiki",
    ready: bool = True,
    tool_name: str = "search",
    bootstrap_errors: list[str] | None = None,
) -> dict[str, object]:
    return {
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

    async def fetch(_base_url: str, _api_key: str) -> object:
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


def test_connector_cache_reads_safe_legacy_record_without_rewrite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A fresh unversioned cache record written by the legacy registry.
    *Do*: Hydrate it through the host-owned catalog reader.
    *Assert*: The record is accepted, bounded in memory, and left byte-for-byte untouched.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    fingerprint = connector_cache_fingerprint("test-key", "https://api.mistral.ai")
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

    async def fetch(_base_url: str, _api_key: str) -> object:
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
        _v2_entry({"connectors": [_connector() for _ in range(257)]}),
        _v2_entry({
            "connectors": [
                {
                    **_connector(),
                    "tools": [
                        {
                            "name": "oversized",
                            "inputSchema": {"description": "x" * (64 * 1_024)},
                        }
                    ],
                }
            ]
        }),
    ],
    ids=["future", "malformed", "expired", "connector-limit", "schema-limit"],
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

    async def fetch(_base_url: str, _api_key: str) -> object:
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
        "id",
        "name",
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

    async def fetch(_base_url: str, _api_key: str) -> object:
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
    """*Prepare*: A legacy cache with colliding display names and one connector without an ID.
    *Do*: Resolve it into the immutable host catalog.
    *Assert*: Aliases are deterministic and only service identities enter the catalog.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    fingerprint = connector_cache_fingerprint("test-key", "https://api.mistral.ai")
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

    async def fetch(_base_url: str, _api_key: str) -> object:
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

    async def fetch(_base_url: str, _api_key: str) -> object:
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


@pytest.mark.asyncio
@pytest.mark.parametrize("names", [("search", "search"), ("search", " search ")])
async def test_connector_catalog_rejects_duplicate_trimmed_tool_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, names: tuple[str, str]
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    connector = _connector()
    connector["tools"] = [
        {"name": name, "description": name, "inputSchema": {}} for name in names
    ]

    async def fetch(_base_url: str, _api_key: str) -> object:
        return {"connectors": [connector]}

    service = ConnectorCatalogService(
        implicit_source_enabled=False,
        cache_path=tmp_path / "connectors.json",
        fetch_bootstrap=fetch,
    )

    with pytest.raises(ConnectorCatalogValidationError, match="duplicate tool names"):
        await service.resolve_catalog(_orchestrator())


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

    async def fetch(_base_url: str, _api_key: str) -> object:
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

    async def fetch(_base_url: str, _api_key: str) -> object:
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
