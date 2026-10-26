"""Reads outside a session fetch the remote config they find no cache for.

The new-session picker reads models through ``config/read`` and modes through
``agents/list``, both without a session. With nothing cached for the API key
(first launch, a key change, an expired entry), the read fetches the org
config, the GrowthBook eval and the model catalog, waits for them within each
fetch's limit, and answers with what they returned.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
import time
import tomllib
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
import tomli_w

from vibe.app_server import _admin_config
from vibe.app_server._admin_config import OrgConfigSource
from vibe.app_server._connection_protocol import ClientInfo
from vibe.app_server._dispatch import DispatchResult, RequestFailure
from vibe.app_server._host import HostRequestHandler
import vibe.app_server._missing_layers as missing_layers
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
from vibe.core.telemetry.types import LaunchContext
from vibe.core.trusted_folders import TrustedFoldersManager

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
        monkeypatch.setattr(_admin_config, "fetch_managed_config", self._org)
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

    async def _org(self, _base_url: str, _api_key: str) -> ManagedConfigResult:
        await self._block("org")
        return ManagedConfigResult(
            config=ManagedConfig(state="enabled", toml=self.org_toml)
        )

    async def _eval(
        self, config: VibeConfigSchema, launch_context: LaunchContext, **_: Any
    ) -> None:
        self.client_names.append(launch_context.client_name)
        if await self._block("eval"):
            store_cached_eval_response(config, _smart_approve_eval())

    async def _catalog(self, config: Any) -> ModelCatalogResponse | None:
        if not await self._block("catalog"):
            return None
        store_cached_model_catalog(config, _catalog())
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
    monkeypatch.setattr(_admin_config, "MANAGED_CONFIG_FETCH_BUDGET", seconds)


@pytest.fixture
def short_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_limits(monkeypatch, _LIMIT)


def _handler() -> HostRequestHandler:
    return HostRequestHandler(HarnessFilesManager(sources=("user",)))


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
async def test_a_read_with_nothing_cached_answers_with_the_fetched_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = _Remote(monkeypatch)
    handler = _handler()

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
) -> None:
    remote = _Remote(monkeypatch, hang=True)
    handler = _handler()

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
) -> None:
    remote = _Remote(monkeypatch)
    await _read_config(_handler())
    remote.calls = {"org": 0, "eval": 0, "catalog": 0}

    await _read_config(_handler())

    assert remote.calls == {"org": 0, "eval": 0, "catalog": 0}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_another_api_key_fetches_for_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = _Remote(monkeypatch)
    handler = _handler()
    await _read_config(handler)

    monkeypatch.setenv("MISTRAL_API_KEY", "another-key")
    await _read_config(handler)

    assert remote.calls == {"org": 2, "eval": 2, "catalog": 2}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_concurrent_reads_share_one_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = _Remote(monkeypatch)
    handler = _handler()

    views = await asyncio.gather(_read_config(handler), _read_config(handler))

    assert remote.calls == {"org": 1, "eval": 1, "catalog": 1}
    assert [view.config.default_agent for view in views] == ["ask", "ask"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("short_limits")
async def test_a_session_start_after_a_failed_org_config_read_still_fetches_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = _Remote(monkeypatch, hang=True)
    handler = _handler()
    await _read_config(handler)
    await _read_config(handler)
    # Reads stop retrying the failed fetch.
    assert remote.calls["org"] == 1
    remote.hang = False
    remote.release.set()

    session_config = await runtime_module.HarnessProcess()._build_session_config(
        SessionOptions(cwd=str(tmp_path)), org_config=OrgConfigSource.FETCHED
    )

    assert remote.calls["org"] == 2
    assert session_config.config_orchestrator.config.default_agent == "ask"


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_reads_the_picker_does_not_make_fetch_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = _Remote(monkeypatch)

    # The session list builds config too, before finding no such session.
    with pytest.raises(RequestFailure):
        await _handler().dispatch(
            "session/history/list",
            SessionHistoryListParams(session_id="missing").model_dump(
                mode="json", by_alias=True
            ),
            client_info=_CLIENT,
        )

    assert remote.calls == {"org": 0, "eval": 0, "catalog": 0}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_a_slow_org_config_is_not_counted_against_the_other_fetches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The org config takes most of the read's limit, so the read answers
    # before GrowthBook and the catalog land. Each lands within its own limit,
    # so neither is recorded as failed: a session start still waits for them.
    # Read limit 1 s; GrowthBook and the catalog land at 1.3 s, 0.4 s before
    # their own limits, which start once the org config is in at 0.7 s.
    _set_limits(monkeypatch, 1.0)
    remote = _Remote(monkeypatch, delays={"org": 0.7, "eval": 0.6, "catalog": 0.6})
    handler = _handler()

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
) -> None:
    # The terminal UI reads its config on the connection its session is
    # attached to; that session is already fetching the same config.
    remote = _Remote(monkeypatch)

    await _handler().dispatch(
        "config/read",
        ConfigReadParams().model_dump(mode="json", by_alias=True),
        session_id="attached-session",
        client_info=_CLIENT,
    )

    assert remote.calls == {"org": 0, "eval": 0, "catalog": 0}


@pytest.mark.asyncio
async def test_the_fetches_follow_the_org_config(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Local config has the catalog off and telemetry on; the org config turns
    # the catalog on and telemetry off, so no eval is sent.
    _set_local_config(
        config_dir, enable_telemetry=True, experimental_enable_model_catalog=False
    )
    remote = _Remote(
        monkeypatch,
        org_toml="experimental_enable_model_catalog = true\nenable_telemetry = false\n",
    )

    view = (await _read_config(_handler())).config

    assert "catalog-model" in {model.alias for model in view.models}
    assert remote.calls == {"org": 1, "eval": 0, "catalog": 1}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_only_the_source_without_a_cache_is_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    remote = _Remote(monkeypatch)
    handler = _handler()
    await _read_config(handler)
    clear_cached_eval_responses()

    await _read_config(handler)

    assert remote.calls == {"org": 1, "eval": 2, "catalog": 1}


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_a_source_that_already_failed_is_not_fetched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The session path recorded these failures; the read shares them.
    remote = _Remote(monkeypatch)
    mark_experiments_eval_failed()
    mark_model_catalog_failed()

    await _read_config(_handler())

    assert remote.calls == {"org": 1, "eval": 0, "catalog": 0}


class _RecordingHost(HostRequestHandler):
    def __init__(self) -> None:
        super().__init__(HarnessFilesManager(sources=("user",)))
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
    method: str, attached: str | None
) -> None:
    host = _RecordingHost()
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
) -> None:
    # The org toml parses but this folder's merged config rejects it (the
    # string where a bool is expected). It is cached anyway: another folder
    # may accept it, and every folder re-validates the cached copy at apply
    # time, as a session does.
    remote = _Remote(
        monkeypatch, org_toml='default_agent = "ask"\nenable_telemetry = "maybe"\n'
    )
    handler = _handler()

    first = (await _read_config(handler)).config
    second = (await _read_config(handler)).config

    assert remote.calls["org"] == 1
    assert first.default_agent != "ask"
    assert second.default_agent != "ask"


@pytest.mark.asyncio
@pytest.mark.usefixtures("remote_config_enabled")
async def test_an_unparseable_org_config_stops_later_reads_fetching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A malformed payload is the remote's, not the folder's: it will not
    # improve within the process, so later reads stop fetching it.
    remote = _Remote(monkeypatch, org_toml="default_agent =\n")
    handler = _handler()

    await _read_config(handler)
    await _read_config(handler)

    assert remote.calls["org"] == 1
