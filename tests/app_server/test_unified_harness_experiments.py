from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tomllib
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
import respx
import tomli_w

from tests.skills.registry.conftest import (
    fetched_versions,
    pin_deploy,
    registry_serves_deploy,
    store_deploy,
)
from tests.stubs.experiment_responses import routing_eval_response
from tests.stubs.process_services import runnable_catalog as _runnable_catalog
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.models import AccountPlanKind
from vibe.app_server.protocol import ClientInfo, SessionOptions
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
from vibe.core.identity import IdentityResult
from vibe.core.platform_auth._api_key import api_key_principal
import vibe.core.platform_auth._mistral as platform_mistral
from vibe.core.telemetry.types import LaunchContext
from vibe.setup.auth.whoami import WhoAmIResult, store_cached_whoami

if TYPE_CHECKING:
    # Typing only: the module pulls in the optional Harness extra, and these
    # tests skip rather than fail when it is absent.
    from vibe.app_server._unified_harness_backend_adapter import UnifiedSessionContext

_ROUTED_ALIAS = "experiment-routed"
_EXTRA_MODELS_VARIANT = json.dumps({
    "models": [
        {
            "name": "experiment-routed-model",
            "provider": "mistral",
            "alias": _ROUTED_ALIAS,
        }
    ]
})


# ``defaultValue`` alone resolves for ``get_variant`` but is deliberately not a
# config override — only a forced rule or a confirmed track is.
def _extra_models_response() -> EvalResponse:
    return EvalResponse.model_validate({
        "features": {
            "vibe_cli_extra_models": {
                "defaultValue": "{}",
                "rules": [{"force": _EXTRA_MODELS_VARIANT, "tracks": []}],
            }
        }
    })


class _StubEvalClient(RemoteEvalClient):
    def __init__(self, response: EvalResponse | None) -> None:
        self._response = response
        self.attributes: ExperimentAttributes | None = None
        self.closed = False

    async def evaluate(self, attributes: ExperimentAttributes) -> EvalResponse | None:
        self.attributes = attributes
        return self._response

    async def aclose(self) -> None:
        self.closed = True


class _RecordingSession:
    session_id = "session-1"
    started_by_session_id: str | None = None
    cwd: str | None = None

    def __init__(self, *, active_turn_id: str | None = None) -> None:
        self.active_turn_id = active_turn_id
        self.applied: list[object] = []
        self.settings: list[object] = []
        self.capabilities: list[object] = []
        self.system_instructions: list[str | None] = []
        self.notices: list[str] = []
        self.shut_down = False

    def configure_turn_settlement(self, settle: object) -> None:
        """Held, not called: this double admits no turns."""
        self.settle_configuration = settle

    def configure_waiting_turn_steer(self, steered: object) -> None:
        """Unused: no queued input joins a waiting turn here."""

    @property
    def turn_queue(self) -> Any:
        """Empty: this double admits no turns."""
        return SimpleNamespace(items=[], paused=False, editing=None)

    async def apply_config(
        self, configuration: Any, *, allow_reserved_turn: bool = False
    ) -> None:
        del allow_reserved_turn
        self.system_instructions.append(configuration.core.system_instructions)
        self.settings.append(configuration.core.settings)
        self.applied.append(configuration.local)
        self.capabilities.append(configuration.core.capabilities)

    async def check_config_fits(self, _configuration: Any) -> None:
        """No Runtime here: every configuration fits."""

    def publish_notice(self, message: str, **_kwargs: object) -> None:
        self.notices.append(message)

    async def shutdown(self) -> None:
        self.shut_down = True


class _RecordingServices:
    def __init__(self) -> None:
        self.finished: list[object] = []

    def client_info(self) -> ClientInfo:
        return ClientInfo(name="test-client", version="0")

    def task_finished(self, task: object) -> None:
        self.finished.append(task)


# The shared test config keeps telemetry and the A/B opt-in off so a leaked key
# can never reach GrowthBook; a test about experiments opts itself back in.
def _enable_experiments(
    config_dir: Path, *, enable: bool = True, system_prompt_id: str | None = None
) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["enable_telemetry"] = True
    config["experiments"] = {"enable": enable}
    if system_prompt_id is not None:
        config["system_prompt_id"] = system_prompt_id
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


def _stub_identity_and_whoami(
    monkeypatch: pytest.MonkeyPatch, context: UnifiedSessionContext
) -> None:
    monkeypatch.setattr(
        "vibe.core.identity_cache.fetch_identity",
        _async_return(IdentityResult(id="user-1", organization=None, workspace=None)),
    )
    whoami = WhoAmIResult(
        plan_type=AccountPlanKind.CHAT,
        plan_name="INDIVIDUAL",
        customer_id="cust-1",
        prompt_switching_to_pro_plan=False,
    )
    context.whoami_cache.populate(
        base_url=context.config_orchestrator.config.console_base_url,
        principal=api_key_principal("mock"),
        result=whoami,
    )
    # What a live /whoami leaves for every cache of the process, the shared
    # eval's included.
    store_cached_whoami(api_key_principal("mock"), whoami)


def _async_return(value: object) -> Any:
    async def _call(*_args: object, **_kwargs: object) -> object:
        return value

    return _call


async def _build_adapter(
    tmp_path: Path,
    *,
    response: EvalResponse | None,
    session: _RecordingSession,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> tuple[Any, _StubEvalClient]:
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )

    process = HarnessProcess(services=process_services)
    built = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    client = _StubEvalClient(response)
    # The process evaluates for every session: the next fetch asks this client.
    monkeypatch.setattr(
        process_services.experiments, "_client_factory", lambda _url: client
    )
    context = replace(built, experiment_manager=ExperimentManager())
    _stub_identity_and_whoami(monkeypatch, context)
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    return adapter, client


def _launch_context() -> LaunchContext:
    return LaunchContext(
        agent_entrypoint="cli",
        agent_version="0",
        client_name="test-client",
        client_version="0",
        terminal_emulator=None,
    )


def _model_aliases(adapter: Any) -> list[str]:
    return [model.alias for model in adapter._runtime.config.models]


@pytest.mark.asyncio
async def test_unified_experiment_variants_reach_the_live_model_catalog(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)
    session = _RecordingSession()
    adapter, _client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=session,
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    assert _ROUTED_ALIAS not in _model_aliases(adapter)

    await adapter.initialize_experiments(_launch_context())

    config = adapter._context.config_orchestrator.config
    assert _ROUTED_ALIAS in config.available_models()
    assert _ROUTED_ALIAS in _model_aliases(adapter)
    assert session.applied, "the derivation was never pushed into the live session"


@pytest.mark.asyncio
async def test_unified_experiment_reports_its_surface_to_growthbook(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.experiments.active import ExperimentSurface

    _enable_experiments(config_dir)
    adapter, client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )

    await adapter.initialize_experiments(_launch_context())

    assert client.attributes is not None
    assert client.attributes.harness is ExperimentSurface.UNIFIED
    assert client.attributes.userId == "user-1"
    assert client.attributes.planName == "INDIVIDUAL"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("experiment_prompt", "user_prompt", "custom"),
    [("cli_ml4_2026-10_v2", None, False), (None, "cli_2026-08_v3", True)],
)
async def test_unified_custom_system_prompt_ignores_the_experiment_prompt(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    experiment_prompt: str | None,
    user_prompt: str | None,
    custom: bool,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.config.layers.growthbook import GrowthbookLayer

    _enable_experiments(config_dir, system_prompt_id=user_prompt)
    adapter, client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    if experiment_prompt is not None:
        # A session opens with the previous eval's cached variants applied.
        orchestrator = adapter._context.config_orchestrator
        layer = orchestrator.get_layer(GrowthbookLayer.NAME)
        assert isinstance(layer, GrowthbookLayer)
        layer.set_variants({"vibe_cli_system_prompt": experiment_prompt})
        await orchestrator.reload()
        assert orchestrator.config.system_prompt_id == experiment_prompt

    await adapter.initialize_experiments(_launch_context())

    assert client.attributes is not None
    assert client.attributes.custom_system_prompt is custom


@pytest.mark.asyncio
async def test_unified_variants_resolved_mid_turn_wait_for_the_next_turn(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)
    session = _RecordingSession(active_turn_id="turn-1")
    adapter, _client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=session,
        monkeypatch=monkeypatch,
        process_services=process_services,
    )

    await adapter.initialize_experiments(_launch_context())
    mid_turn_pushes = len(session.applied)
    mid_turn_aliases = _model_aliases(adapter)
    session.active_turn_id = None
    await adapter._settle_configuration()

    # The Core reads its settings at turn start, so a mid-turn push would swap
    # the model underneath the running turn.
    assert mid_turn_pushes == 0
    assert _ROUTED_ALIAS not in mid_turn_aliases
    assert _ROUTED_ALIAS in _model_aliases(adapter)
    assert len(session.applied) == 1


@pytest.mark.asyncio
async def test_unified_an_eval_turning_registry_skills_on_starts_their_sync(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    _enable_experiments(config_dir)
    rollout = EvalResponse.model_validate({
        "features": {
            "vibe_cli_registry_skills": {
                "defaultValue": False,
                "rules": [{"force": True, "tracks": []}],
            }
        }
    })
    await store_deploy(1)
    await pin_deploy()
    deploy = registry_serves_deploy(respx_mock)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=rollout,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    adapter.start_registry_sync()
    opened_with = set(adapter._registry_sync._tasks)

    await adapter.initialize_experiments(_launch_context())
    [rolled_out] = adapter._registry_sync._tasks
    await rolled_out

    assert opened_with == set()
    assert (
        adapter._context.config_orchestrator.config.experimental_enable_registry_skills
    )
    assert fetched_versions(deploy) == [None]


@pytest.mark.asyncio
async def test_unified_experiments_are_skipped_when_the_ab_opt_out_is_set(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir, enable=False)
    session = _RecordingSession()
    adapter, client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=session,
        monkeypatch=monkeypatch,
        process_services=process_services,
    )

    await adapter.initialize_experiments(_launch_context())

    assert client.attributes is None
    assert session.applied == []


@pytest.mark.asyncio
async def test_unified_failed_eval_leaves_the_session_as_it_opened(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)
    session = _RecordingSession()
    adapter, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=session,
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    opened_with = _model_aliases(adapter)

    await adapter.initialize_experiments(_launch_context())

    assert session.applied == []
    assert _model_aliases(adapter) == opened_with


@pytest.mark.asyncio
async def test_unified_experiment_initialization_never_raises(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)
    session = _RecordingSession()
    adapter, client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=session,
        monkeypatch=monkeypatch,
        process_services=process_services,
    )

    async def _explode(_attributes: ExperimentAttributes) -> EvalResponse | None:
        raise RuntimeError("eval exploded")

    monkeypatch.setattr(client, "evaluate", _explode)

    await adapter.initialize_experiments(_launch_context())
    assert session.applied == []
    assert process_services.experiments.failures.failed is True


@pytest.mark.asyncio
async def test_unified_the_eval_client_is_closed_once_the_eval_landed(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)
    session = _RecordingSession()
    adapter, client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=session,
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    await adapter.initialize_experiments(_launch_context())

    assert client.closed


@pytest.mark.asyncio
async def test_unified_resolved_variants_carry_to_the_next_session_through_the_cache(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._runtime import HarnessProcess

    # Unified persists nothing per session, so the global eval cache is the whole
    # of its continuity.
    _enable_experiments(config_dir)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    await adapter.initialize_experiments(_launch_context())

    reopened = await HarnessProcess(
        services=process_services
    ).build_unified_session_context(SessionOptions(cwd=str(tmp_path)))

    assert _ROUTED_ALIAS in reopened.config_orchestrator.config.available_models()


@pytest.mark.asyncio
async def test_the_experiment_task_is_handed_to_the_server_as_a_done_callback(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )

    _enable_experiments(config_dir)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    services = _RecordingServices()
    host = UnifiedHarnessBackendHostAdapter(
        cast(Any, None), cast(Any, None), cast(Any, services)
    )

    host._start_experiments(adapter)
    started = adapter._experiments_task

    # ``task_finished`` is a *done* callback: calling it on a running task
    # raises InvalidStateError out of session open.
    assert services.finished == []
    assert started is not None
    await started
    assert services.finished == [started]
    assert started.exception() is None


@pytest.mark.asyncio
async def test_closing_a_session_drops_an_eval_still_in_flight(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import asyncio

    _enable_experiments(config_dir)
    session = _RecordingSession()
    adapter, client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=session,
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    hung = asyncio.Event()

    async def _never_answers(_attributes: ExperimentAttributes) -> EvalResponse | None:
        hung.set()
        await asyncio.Event().wait()
        return None

    monkeypatch.setattr(client, "evaluate", _never_answers)
    task = asyncio.create_task(adapter.initialize_experiments(_launch_context()))
    adapter._experiments_task = task
    await hung.wait()

    await adapter.shutdown()

    assert task.cancelled()
    assert session.shut_down


def _serve_catalog(
    monkeypatch: pytest.MonkeyPatch, process_services: ProcessServices, fetch: Any
) -> None:
    """Have the process's model catalog endpoint answer with *fetch*."""
    monkeypatch.setattr(process_services.model_catalog, "_fetch_catalog", fetch)


async def _open_catalog(adapter: Any) -> None:
    """What a session open does with the catalog: follow it, and load it."""
    adapter._start_model_catalog_fetch()
    if (task := adapter._model_catalog_task) is not None:
        await task


async def _adapter_fetching(
    tmp_path: Path,
    config_dir: Path,
    session: _RecordingSession,
    monkeypatch: pytest.MonkeyPatch,
    *,
    process_services: ProcessServices,
) -> tuple[Any, AsyncMock]:
    _write_user_config(config_dir)
    _serve_catalog(
        monkeypatch,
        process_services,
        AsyncMock(return_value=_runnable_catalog("fetched-model")),
    )
    adapter, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=session,
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    announced = AsyncMock()
    monkeypatch.setattr(adapter, "_announce_runtime", announced)
    monkeypatch.setattr(adapter, "wait_announced", AsyncMock())
    return adapter, announced


@pytest.mark.asyncio
async def test_unified_fetched_catalog_reaches_the_live_model_list(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession()
    adapter, announced = await _adapter_fetching(
        tmp_path, config_dir, session, monkeypatch, process_services=process_services
    )

    await _open_catalog(adapter)

    assert "fetched-model" in _model_aliases(adapter)
    assert session.applied, "the derivation was never pushed into the live session"
    announced.assert_awaited_once()


@pytest.mark.asyncio
async def test_unified_catalog_fetched_mid_turn_waits_for_the_next_turn(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession(active_turn_id="turn-1")
    adapter, announced = await _adapter_fetching(
        tmp_path, config_dir, session, monkeypatch, process_services=process_services
    )

    await _open_catalog(adapter)
    mid_turn_aliases = _model_aliases(adapter)
    session.active_turn_id = None
    await adapter._settle_configuration()

    assert "fetched-model" not in mid_turn_aliases
    assert len(session.applied) == 1
    assert "fetched-model" in _model_aliases(adapter)


@pytest.mark.asyncio
async def test_unified_fetch_returning_the_same_catalog_announces_nothing(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession()
    adapter, announced = await _adapter_fetching(
        tmp_path, config_dir, session, monkeypatch, process_services=process_services
    )
    await _open_catalog(adapter)

    await _open_catalog(adapter)

    assert len(session.applied) == 1
    announced.assert_awaited_once()


@pytest.mark.asyncio
async def test_unified_pin_on_a_catalog_alias_selects_the_fetched_model(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.model_catalog import parse_model_catalog_response

    _write_user_config(config_dir, active_model="fetched-old")
    catalog = parse_model_catalog_response({
        "models": [
            {
                "id": "fetched-model",
                "label": "Fetched",
                "aliases": ["fetched-old"],
                "capabilities": {"function_calling": True},
            }
        ]
    })
    _serve_catalog(monkeypatch, process_services, AsyncMock(return_value=catalog))
    adapter, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    monkeypatch.setattr(adapter, "_announce_runtime", AsyncMock())
    monkeypatch.setattr(adapter, "wait_announced", AsyncMock())

    await _open_catalog(adapter)

    resolved = adapter._context.config_orchestrator.config
    assert resolved.active_model == "fetched-model"
    assert resolved.get_active_model().alias == "fetched-model"


def _write_user_config(config_dir: Path, **fields: object) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config.update({"experimental_enable_model_catalog": True, **fields})
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


@pytest.mark.asyncio
async def test_unified_readiness_waits_for_the_catalog_an_unpinned_session_needs(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _write_user_config(config_dir, active_model="")
    released = asyncio.Event()

    async def _slow_fetch(_config: object, _credentials: object = None) -> object:
        await released.wait()
        return _runnable_catalog("fetched-model")

    _serve_catalog(monkeypatch, process_services, _slow_fetch)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    monkeypatch.setattr(adapter, "_announce_runtime", AsyncMock())
    monkeypatch.setattr(adapter, "wait_announced", AsyncMock())

    adapter._start_model_catalog_fetch()
    read = await adapter.dispatch_extension("session/ready/read", {})
    waiter = asyncio.create_task(adapter.dispatch_extension("session/ready/wait", {}))
    await asyncio.sleep(0)

    assert read.response.ready is False
    assert not waiter.done()
    released.set()
    result = await waiter
    assert result.response.ready is True
    assert "fetched-model" in _model_aliases(adapter)
    read_after = await adapter.dispatch_extension("session/ready/read", {})
    assert read_after.response.ready is True


@pytest.mark.asyncio
async def test_unified_readiness_skips_the_catalog_for_a_locally_defined_pin(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _write_user_config(config_dir)  # the test config pins a built-in model
    released = asyncio.Event()

    async def _slow_fetch(_config: object, _credentials: object = None) -> object:
        await released.wait()
        return None

    _serve_catalog(monkeypatch, process_services, _slow_fetch)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )

    adapter._start_model_catalog_fetch()
    read = await adapter.dispatch_extension("session/ready/read", {})

    assert read.response.ready is True
    released.set()
    task = adapter._model_catalog_task
    assert task is not None
    await task


@pytest.mark.asyncio
async def test_unified_sessions_stop_waiting_once_a_catalog_fetch_failed(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    _write_user_config(config_dir, active_model="")
    # The endpoint answers nothing (the fake server's default).
    first, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    first._start_model_catalog_fetch()
    assert first._model_catalog_wait is not None
    await first.dispatch_extension("session/ready/wait", {})

    second, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    second._start_model_catalog_fetch()
    cleared = _replacement_adapter(first)
    cleared._start_model_catalog_fetch()

    assert second._model_catalog_wait is None
    assert cleared._model_catalog_wait is None
    read = await second.dispatch_extension("session/ready/read", {})
    assert read.response.ready is True
    for adapter in (second, cleared):
        task = adapter._model_catalog_task
        assert task is not None
        await task


def _replacement_adapter(source: Any) -> Any:
    """An adapter over *source*'s context, as ``/clear`` builds one."""
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )

    context = source._context
    return UnifiedHarnessBackendAdapter(
        cast(Any, _RecordingSession()),
        context,
        context.derive(UnifiedSessionSettings()),
    )


@pytest.mark.asyncio
async def test_unified_a_restored_pin_only_the_catalog_defines_runs_once_ready(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._session_model import set_session_active_model_override

    _write_user_config(config_dir)  # pinned to a built-in model: no wait alone
    released = asyncio.Event()

    async def _slow_fetch(_config: object, _credentials: object = None) -> object:
        await released.wait()
        return _runnable_catalog("fetched-model")

    _serve_catalog(monkeypatch, process_services, _slow_fetch)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    monkeypatch.setattr(adapter, "_announce_runtime", AsyncMock())
    monkeypatch.setattr(adapter, "wait_announced", AsyncMock())
    orchestrator = adapter._context.config_orchestrator
    # What a reopened session does with the model it was left on.
    assert not await set_session_active_model_override(
        orchestrator, "fetched-model", reason="restore"
    )

    adapter._start_model_catalog_fetch()
    read = await adapter.dispatch_extension("session/ready/read", {})
    waiter = asyncio.create_task(adapter.dispatch_extension("session/ready/wait", {}))
    await asyncio.sleep(0)

    assert read.response.ready is False
    assert not waiter.done()
    released.set()
    await waiter
    assert orchestrator.config.get_active_model().alias == "fetched-model"


@pytest.mark.asyncio
async def test_unified_sessions_stop_waiting_once_a_fetch_outlasts_the_cap(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.core.model_catalog.wait as wait_module

    _write_user_config(config_dir, active_model="")
    released = asyncio.Event()

    async def _stuck_fetch(_config: object, _credentials: object = None) -> None:
        await released.wait()

    _serve_catalog(monkeypatch, process_services, _stuck_fetch)
    monkeypatch.setattr(wait_module, "MODEL_CATALOG_WAIT_CAP_SECONDS", 0.05)
    first, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    first._start_model_catalog_fetch()

    result = await asyncio.wait_for(
        first.dispatch_extension("session/ready/wait", {}), timeout=2
    )

    assert result.response.ready is True
    second, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    second._start_model_catalog_fetch()
    cleared = _replacement_adapter(first)
    cleared._start_model_catalog_fetch()
    assert second._model_catalog_wait is None
    assert cleared._model_catalog_wait is None
    released.set()
    for adapter in (first, second, cleared):
        task = adapter._model_catalog_task
        assert task is not None
        await task


@pytest.mark.asyncio
async def test_unified_no_mistral_credential_means_no_wait(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _write_user_config(config_dir, active_model="")
    monkeypatch.setattr(
        platform_mistral, "mistral_credentials", lambda _config, _credentials=None: None
    )
    adapter, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )

    adapter._start_model_catalog_fetch()

    assert adapter._model_catalog_wait is None
    read = await adapter.dispatch_extension("session/ready/read", {})
    assert read.response.ready is True
    task = adapter._model_catalog_task
    assert task is not None
    await task


def _unpin_with_experiments(config_dir: Path) -> None:
    _enable_experiments(config_dir)
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["active_model"] = ""
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


def _start_experiments(adapter: Any) -> None:
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendHostAdapter,
    )

    host = UnifiedHarnessBackendHostAdapter(
        cast(Any, None), cast(Any, None), cast(Any, _RecordingServices())
    )
    host._start_experiments(adapter)


def _hold_eval(
    client: _StubEvalClient, monkeypatch: pytest.MonkeyPatch
) -> asyncio.Event:
    released = asyncio.Event()
    answer = client.evaluate

    async def _held(attributes: ExperimentAttributes) -> EvalResponse | None:
        await released.wait()
        return await answer(attributes)

    monkeypatch.setattr(client, "evaluate", _held)
    return released


@pytest.mark.asyncio
async def test_unified_an_uncached_eval_holds_an_unpinned_session_until_it_lands(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    adapter, client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    released = _hold_eval(client, monkeypatch)

    _start_experiments(adapter)
    waiter = asyncio.create_task(adapter._wait_for_first_turn_fetches())
    await asyncio.sleep(0.01)

    assert not waiter.done()
    released.set()
    await asyncio.wait_for(waiter, timeout=2)
    config = adapter._context.config_orchestrator.config
    assert config.get_active_model().alias == _ROUTED_ALIAS


@pytest.mark.asyncio
async def test_unified_a_pinned_session_does_not_wait_for_the_eval(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)  # the test config pins a built-in model
    adapter, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )

    _start_experiments(adapter)

    assert adapter._experiments_wait is None
    task = adapter._experiments_task
    assert task is not None
    await task


@pytest.mark.asyncio
async def test_unified_a_reopened_session_with_a_pinned_model_does_not_wait(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._session_model import set_session_active_model_override
    from vibe.core.config.vibe_schema import DEFAULT_ACTIVE_MODEL_CONFIG

    _unpin_with_experiments(config_dir)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    # What a reopened session does with the model its first turn pinned.
    assert not await set_session_active_model_override(
        adapter._context.config_orchestrator,
        DEFAULT_ACTIVE_MODEL_CONFIG.alias,
        reason="restore",
    )

    _start_experiments(adapter)

    assert adapter._experiments_wait is None
    task = adapter._experiments_task
    assert task is not None
    await task


@pytest.mark.asyncio
async def test_unified_a_cached_eval_means_no_wait(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    first, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    _start_experiments(first)
    assert first._experiments_wait is not None
    first_task = first._experiments_task
    assert first_task is not None
    await first_task

    second, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    _start_experiments(second)

    assert second._experiments_wait is None
    second_task = second._experiments_task
    assert second_task is not None
    await second_task


@pytest.mark.asyncio
async def test_unified_sessions_stop_waiting_once_an_eval_failed(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    first, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    _start_experiments(first)
    assert first._experiments_wait is not None
    await asyncio.wait_for(first._wait_for_first_turn_fetches(), timeout=2)

    second, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    _start_experiments(second)

    assert second._experiments_wait is None
    for adapter in (first, second):
        task = adapter._experiments_task
        assert task is not None
        await task


@pytest.mark.asyncio
async def test_unified_sessions_stop_waiting_once_an_eval_outlasts_the_cap(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.core.experiments.wait as wait_module

    _unpin_with_experiments(config_dir)
    monkeypatch.setattr(wait_module, "EVAL_WAIT_CAP_SECONDS", 0.05)
    first, client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    released = _hold_eval(client, monkeypatch)
    _start_experiments(first)
    ready = asyncio.create_task(first.dispatch_extension("session/ready/wait", {}))

    # Nothing waits on the first session's turns: the cap still counts.
    await asyncio.sleep(0.2)

    wait = first._experiments_wait
    assert wait is not None
    assert wait.done
    # Readiness still waits for the whole eval, for the startup telemetry.
    assert not ready.done()
    second, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    _start_experiments(second)
    assert second._experiments_wait is None
    first._cancel_experiments_task()
    await ready
    # The second session joined the eval the first one started: closing the
    # first leaves it running for the second.
    released.set()
    second_task = second._experiments_task
    assert second_task is not None
    await asyncio.wait_for(second_task, timeout=2)
    config = second._context.config_orchestrator.config
    assert config.get_active_model().alias == _ROUTED_ALIAS


@pytest.mark.asyncio
async def test_unified_an_eval_outlasting_the_cap_in_a_pinned_session_stops_later_waits(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.core.experiments.wait as wait_module

    _enable_experiments(config_dir)  # the test config pins a built-in model
    monkeypatch.setattr(wait_module, "EVAL_WAIT_CAP_SECONDS", 0.05)
    pinned, client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    released = _hold_eval(client, monkeypatch)
    _start_experiments(pinned)
    assert pinned._experiments_wait is None

    await asyncio.sleep(0.2)

    assert process_services.experiments.failures.failed is True
    _unpin_with_experiments(config_dir)
    unpinned, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    _start_experiments(unpinned)
    assert unpinned._experiments_wait is None
    pinned._cancel_experiments_task()
    released.set()
    task = unpinned._experiments_task
    assert task is not None
    await asyncio.wait_for(task, timeout=2)


@pytest.mark.asyncio
async def test_unified_a_cache_written_after_the_session_was_built_still_waits(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    second, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    first, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    # Another session's eval writes the cache before this one opens.
    await first.initialize_experiments(_launch_context())

    _start_experiments(second)

    assert second._experiments_wait is not None
    task = second._experiments_task
    assert task is not None
    await task


@pytest.mark.asyncio
async def test_unified_a_slow_lookup_without_an_eval_is_not_an_eval_failure(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.core.experiments.wait as wait_module

    # The A/B opt-out still resolves identity for telemetry, but sends no eval.
    _enable_experiments(config_dir, enable=False)
    monkeypatch.setattr(wait_module, "EVAL_WAIT_CAP_SECONDS", 0.05)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    released = asyncio.Event()

    async def _slow_identity(*_args: object, **_kwargs: object) -> object:
        await released.wait()
        return IdentityResult(id="user-1", organization=None, workspace=None)

    monkeypatch.setattr("vibe.core.identity_cache.fetch_identity", _slow_identity)
    _start_experiments(adapter)

    await asyncio.sleep(0.2)

    assert adapter._experiments_wait is None
    assert process_services.experiments.failures.failed is False
    released.set()
    task = adapter._experiments_task
    assert task is not None
    await task


def _model_catalog_on_response(*, routed: str | None = None) -> EvalResponse:
    """An eval that turns the model catalog on, and routes the default to
    *routed* through the old routing flag.
    """
    features: dict[str, Any] = {
        "vibe_cli_model_catalog": {"defaultValue": True, "rules": []}
    }
    if routed is not None:
        features.update(routing_eval_response(routed).model_dump()["features"])
    return EvalResponse.model_validate({"features": features})


def _recommended_catalog(model_id: str) -> Any:
    from vibe.core.model_catalog import parse_model_catalog_response

    return parse_model_catalog_response({
        "models": [
            {
                "id": model_id,
                "label": model_id,
                "recommended": True,
                "capabilities": {"function_calling": True},
            }
        ]
    })


@pytest.mark.asyncio
async def test_unified_a_catalog_the_eval_turns_on_holds_the_first_turn(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    fetched = asyncio.Event()
    released = asyncio.Event()

    async def _held_fetch(_config: Any, _credentials: Any = None) -> Any:
        fetched.set()
        await released.wait()
        return _recommended_catalog("catalog-model")

    fetch = AsyncMock(side_effect=_held_fetch)
    _serve_catalog(monkeypatch, process_services, fetch)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=_model_catalog_on_response(routed=_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    monkeypatch.setattr(adapter, "wait_announced", AsyncMock())
    config = adapter._context.config_orchestrator.config
    assert not config.experimental_enable_model_catalog

    _start_experiments(adapter)
    waiter = asyncio.create_task(adapter._wait_for_first_turn_fetches())
    await asyncio.wait_for(fetched.wait(), timeout=2)

    # The eval landed and turned the catalog on; the turn waits for it.
    assert not waiter.done()
    released.set()
    await asyncio.wait_for(waiter, timeout=2)
    config = adapter._context.config_orchestrator.config
    assert config.get_active_model().alias == "catalog-model"
    assert _ROUTED_ALIAS not in config.models
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_unified_a_catalog_the_eval_turns_on_starts_from_the_one_held(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._model_catalog_service import ModelCatalogKey
    from vibe.core.config import VibeConfigSchema

    _unpin_with_experiments(config_dir)
    # Another session of the process already holds a catalog.
    _serve_catalog(
        monkeypatch,
        process_services,
        AsyncMock(return_value=_recommended_catalog("cached-model")),
    )
    key = await ModelCatalogKey.of(
        VibeConfigSchema(experimental_enable_model_catalog=True), None
    )
    assert key is not None
    await process_services.model_catalog.ensure(key)
    fetch = AsyncMock(side_effect=AssertionError("no request"))
    _serve_catalog(monkeypatch, process_services, fetch)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=_model_catalog_on_response(routed=_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    monkeypatch.setattr(adapter, "wait_announced", AsyncMock())

    _start_experiments(adapter)
    await asyncio.wait_for(adapter._wait_for_first_turn_fetches(), timeout=2)

    config = adapter._context.config_orchestrator.config
    assert config.get_active_model().alias == "cached-model"
    assert _ROUTED_ALIAS not in config.models
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_unified_a_catalog_already_on_is_not_fetched_again_after_the_eval(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    _write_user_config(config_dir)
    fetch = AsyncMock(return_value=_recommended_catalog("catalog-model"))
    _serve_catalog(monkeypatch, process_services, fetch)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=_model_catalog_on_response(),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    monkeypatch.setattr(adapter, "wait_announced", AsyncMock())

    _start_experiments(adapter)
    await asyncio.wait_for(adapter._wait_for_first_turn_fetches(), timeout=2)
    await adapter._experiments_task
    if (task := adapter._model_catalog_task) is not None:
        await task

    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_unified_a_catalog_flag_turned_off_waits_for_the_next_session(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    # The session opens on a cached eval with the flag on, then a fresh eval
    # turns it off.
    monkeypatch.setattr(
        "vibe.app_server._cached_layers.load_cached_eval_response",
        lambda _config, _credentials=None: _model_catalog_on_response(),
    )
    fetch = AsyncMock(return_value=_recommended_catalog("catalog-model"))
    _serve_catalog(monkeypatch, process_services, fetch)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    monkeypatch.setattr(adapter, "wait_announced", AsyncMock())
    assert adapter._context.config_orchestrator.config.experimental_enable_model_catalog

    _start_experiments(adapter)
    await asyncio.wait_for(adapter._wait_for_first_turn_fetches(), timeout=2)
    await adapter._experiments_task

    config = adapter._context.config_orchestrator.config
    assert config.experimental_enable_model_catalog
    assert "catalog-model" in config.models
    assert _ROUTED_ALIAS not in config.models
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_unified_a_local_setting_off_wins_over_the_catalog_flag(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    _write_user_config(config_dir, experimental_enable_model_catalog=False)
    fetch = AsyncMock(return_value=_recommended_catalog("catalog-model"))
    _serve_catalog(monkeypatch, process_services, fetch)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=_model_catalog_on_response(),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )

    _start_experiments(adapter)
    await adapter._experiments_task
    await asyncio.wait_for(adapter._wait_for_first_turn_fetches(), timeout=2)

    assert adapter._model_catalog_task is None
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_unified_an_eval_the_session_was_built_with_reloads_nothing(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    # A session opened after another one evaluated is built from the eval cache
    # that eval wrote. Reloading its config for the same variants would race an
    # agent switch sent with its first message, which rebuilds the same config
    # synchronously, and deadlock the app server.
    _enable_experiments(config_dir)
    first, _client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    await first.initialize_experiments(_launch_context())
    second, _client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    reloads: list[None] = []
    orchestrator = second._context.config_orchestrator
    reload = orchestrator.reload

    async def counting_reload(*args: Any, **kwargs: Any) -> Any:
        reloads.append(None)
        return await reload(*args, **kwargs)

    monkeypatch.setattr(orchestrator, "reload", counting_reload)

    await second.initialize_experiments(_launch_context())

    assert reloads == []
    assert _ROUTED_ALIAS in _model_aliases(second)


@pytest.mark.asyncio
async def test_unified_a_restarted_app_server_opens_sessions_without_waiting_on_growthbook(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from tests.stubs.process_services import fake_process_services

    # An earlier app server evaluated this account and client. The next one
    # serves its sessions that eval from disk: readiness and the first turn of
    # an unpinned session never wait on GrowthBook, which here never answers.
    _unpin_with_experiments(config_dir)
    earlier, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    _start_experiments(earlier)
    assert earlier._experiments_task is not None
    await earlier._experiments_task
    restarted = fake_process_services()
    adapter, client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=restarted,
    )
    _hold_eval(client, monkeypatch)

    _start_experiments(adapter)
    await asyncio.wait_for(adapter._wait_for_first_turn_fetches(), timeout=1)
    task = adapter._experiments_task
    assert task is not None
    # Readiness waits for this task: served from disk, it is done at once.
    await asyncio.wait_for(task, timeout=0.5)

    assert client.attributes is None
    config = adapter._context.config_orchestrator.config
    assert config.get_active_model().alias == _ROUTED_ALIAS
    assert adapter._context.experiment_manager.attributes() is not None
    await restarted.close()


async def _catalog_adapter(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
    session: _RecordingSession | None = None,
    **config: object,
) -> Any:
    _write_user_config(config_dir, **config)
    adapter, _client = await _build_adapter(
        tmp_path,
        response=None,
        session=session or _RecordingSession(),
        monkeypatch=monkeypatch,
        process_services=process_services,
    )
    monkeypatch.setattr(adapter, "_announce_runtime", AsyncMock())
    monkeypatch.setattr(adapter, "wait_announced", AsyncMock())
    return adapter


async def _refresh_held_catalog(process_services: ProcessServices) -> None:
    """What another session's background refresh leaves held."""
    from vibe.app_server._model_catalog_service import ModelCatalogKey
    from vibe.core.config import VibeConfigSchema

    key = await ModelCatalogKey.of(
        VibeConfigSchema(experimental_enable_model_catalog=True), None
    )
    assert key is not None
    await process_services.model_catalog.refresh(key)


@pytest.mark.asyncio
async def test_unified_sessions_after_the_first_share_its_catalog_fetch(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    released = asyncio.Event()

    async def held(_api_base: object, _credentials: object) -> Any:
        await released.wait()
        return _runnable_catalog("fetched-model")

    fetch = AsyncMock(side_effect=held)
    _serve_catalog(monkeypatch, process_services, fetch)
    sessions = [
        await _catalog_adapter(tmp_path, config_dir, monkeypatch, process_services)
        for _ in range(3)
    ]

    for adapter in sessions:
        adapter._start_model_catalog_fetch()
    await asyncio.sleep(0.05)
    released.set()
    for adapter in sessions:
        await adapter._model_catalog_task
    later = await _catalog_adapter(tmp_path, config_dir, monkeypatch, process_services)
    await _open_catalog(later)

    fetch.assert_awaited_once()
    for adapter in (*sessions, later):
        assert "fetched-model" in _model_aliases(adapter)


@pytest.mark.asyncio
async def test_unified_a_refreshed_catalog_reaches_a_running_session_at_its_boundary(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    _serve_catalog(
        monkeypatch,
        process_services,
        AsyncMock(
            side_effect=[
                _runnable_catalog("first-model"),
                _runnable_catalog("first-model", "second-model"),
            ]
        ),
    )
    session = _RecordingSession()
    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services, session
    )
    await _open_catalog(adapter)
    assert "first-model" in _model_aliases(adapter)
    pushed = len(session.applied)

    session.active_turn_id = "turn-1"
    await _refresh_held_catalog(process_services)
    await adapter._load_remote_values()

    # The running turn keeps the config it started with.
    assert "second-model" not in _model_aliases(adapter)
    assert "second-model" not in adapter._context.config_orchestrator.config.models
    assert len(session.applied) == pushed

    session.active_turn_id = None
    await adapter._settle_configuration()

    assert "second-model" in _model_aliases(adapter)
    assert len(session.applied) == pushed + 1


@pytest.mark.asyncio
async def test_unified_a_refreshed_catalog_keeps_a_pinned_model(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # The session runs a model the user picked from the catalog; a refresh
    # that retires it must not move the session off it.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._session_model import set_session_active_model_override

    _serve_catalog(
        monkeypatch,
        process_services,
        AsyncMock(
            side_effect=[
                _runnable_catalog("picked-model", "other-model"),
                _runnable_catalog("other-model"),
            ]
        ),
    )
    session = _RecordingSession()
    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services, session, active_model=""
    )
    await _open_catalog(adapter)
    orchestrator = adapter._context.config_orchestrator
    assert not await set_session_active_model_override(
        orchestrator, "picked-model", reason="pick"
    )
    pushed = len(session.applied)

    await _refresh_held_catalog(process_services)
    await adapter._settle_configuration()

    assert orchestrator.config.active_model == "picked-model"
    assert orchestrator.config.get_active_model().alias == "picked-model"
    assert len(session.applied) == pushed
    assert any("'picked-model' is no longer" in notice for notice in session.notices)
    # Refused once, not on every later boundary.
    assert not adapter._model_catalog.due


@pytest.mark.asyncio
async def test_unified_a_catalog_renaming_a_pinned_model_keeps_it_through_its_alias(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._session_model import set_session_active_model_override
    from vibe.core.model_catalog import parse_model_catalog_response

    renamed = parse_model_catalog_response({
        "models": [
            {
                "id": "renamed-model",
                "label": "Renamed",
                "aliases": ["picked-model"],
                "capabilities": {"function_calling": True},
            }
        ]
    })
    _serve_catalog(
        monkeypatch,
        process_services,
        AsyncMock(side_effect=[_runnable_catalog("picked-model"), renamed]),
    )
    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services, active_model=""
    )
    await _open_catalog(adapter)
    orchestrator = adapter._context.config_orchestrator
    assert not await set_session_active_model_override(
        orchestrator, "picked-model", reason="pick"
    )

    await _refresh_held_catalog(process_services)
    await adapter._settle_configuration()

    assert orchestrator.config.get_active_model().alias == "renamed-model"
    assert "renamed-model" in _model_aliases(adapter)


@pytest.mark.asyncio
async def test_unified_a_catalog_load_waiting_out_a_move_follows_what_the_move_left(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # A worktree move holds the switch lock and drops what the session
    # followed; a load that waited for it must not commit the old catalog.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._model_catalog_service import ModelCatalogKey

    _serve_catalog(
        monkeypatch,
        process_services,
        AsyncMock(return_value=_runnable_catalog("old-account-model")),
    )
    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services
    )
    await _refresh_held_catalog(process_services)

    async with adapter._agent_switching:
        load = asyncio.create_task(
            adapter._load_model_catalog(reserved_turn=False, strict=False)
        )
        await asyncio.sleep(0.05)
        adapter._close_org_config()
        monkeypatch.setattr(
            ModelCatalogKey, "current", classmethod(lambda _cls, *_args: None)
        )

    assert await load is False
    assert "old-account-model" not in _model_aliases(adapter)


@pytest.mark.asyncio
async def test_unified_a_catalog_fetched_across_a_move_follows_the_new_context(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # The fetch the session opened with lands after a worktree move: the key
    # it resolved is the old context's, so the session follows the new one's.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._model_catalog_service import ModelCatalogKey

    released = asyncio.Event()

    async def held(_api_base: object, _credentials: object) -> Any:
        await released.wait()
        return _runnable_catalog("old-context-model")

    _serve_catalog(monkeypatch, process_services, AsyncMock(side_effect=held))
    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services
    )
    adapter._start_model_catalog_fetch()
    await asyncio.sleep(0.05)
    moved = replace(adapter._context)
    adapter._context = moved
    monkeypatch.setattr(
        ModelCatalogKey, "current", classmethod(lambda _cls, *_args: None)
    )

    released.set()
    await adapter._model_catalog_task

    assert adapter._model_catalog_key is None
    assert "old-context-model" not in _model_aliases(adapter)


@pytest.mark.asyncio
async def test_unified_a_retired_pick_is_kept_over_a_configured_default(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # The user's config names a default model; the session picked a catalog
    # model over it. A refresh retiring the pick must not move the session to
    # that default.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._session_model import set_session_active_model_override

    _serve_catalog(
        monkeypatch,
        process_services,
        AsyncMock(
            side_effect=[
                _runnable_catalog("picked-model", "other-model"),
                _runnable_catalog("other-model"),
            ]
        ),
    )
    session = _RecordingSession()
    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services, session
    )
    orchestrator = adapter._context.config_orchestrator
    configured = orchestrator.config.active_model
    assert configured
    await _open_catalog(adapter)
    assert not await set_session_active_model_override(
        orchestrator, "picked-model", reason="pick"
    )

    await _refresh_held_catalog(process_services)
    await adapter._settle_configuration()

    assert orchestrator.config.get_active_model().alias == "picked-model"
    assert any("'picked-model' is no longer" in notice for notice in session.notices)


@pytest.mark.asyncio
async def test_unified_a_catalog_refused_for_a_pin_loads_once_another_model_is_picked(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._session_model import set_session_active_model_override

    _serve_catalog(
        monkeypatch,
        process_services,
        AsyncMock(
            side_effect=[
                _runnable_catalog("picked-model", "other-model"),
                _runnable_catalog("other-model", "new-model"),
            ]
        ),
    )
    session = _RecordingSession()
    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services, session, active_model=""
    )
    await _open_catalog(adapter)
    orchestrator = adapter._context.config_orchestrator
    assert not await set_session_active_model_override(
        orchestrator, "picked-model", reason="pick"
    )
    await _refresh_held_catalog(process_services)
    await adapter._settle_configuration()
    assert "new-model" not in _model_aliases(adapter)

    # The user follows the notice and picks a model the refresh kept.
    assert not await set_session_active_model_override(
        orchestrator, "other-model", reason="pick"
    )
    await adapter._settle_configuration()

    assert "new-model" in _model_aliases(adapter)
    assert orchestrator.config.get_active_model().alias == "other-model"
    assert sum("is no longer" in notice for notice in session.notices) == 1


@pytest.mark.asyncio
async def test_unified_a_session_following_a_new_account_fetches_its_catalog(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # A login moves the session to an account nothing in the process fetched:
    # the boundary that follows it starts the fetch, and a later one loads it.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._model_catalog_service import ModelCatalogKey

    fetch = AsyncMock(
        side_effect=[_runnable_catalog("first-model"), _runnable_catalog("new-model")]
    )
    _serve_catalog(monkeypatch, process_services, fetch)
    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services
    )
    await _open_catalog(adapter)
    first = adapter._model_catalog_key
    assert first is not None
    other = ModelCatalogKey("other-account", first.api_base, first.credentials)
    monkeypatch.setattr(
        ModelCatalogKey, "current", classmethod(lambda _cls, *_args: other)
    )

    await adapter._settle_configuration()
    for _ in range(50):
        if fetch.await_count == 2:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    await adapter._settle_configuration()

    assert fetch.await_count == 2
    assert "new-model" in _model_aliases(adapter)


@pytest.mark.asyncio
async def test_unified_a_catalog_whose_install_failed_does_not_come_back(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # The install's reload loads the new catalog, then fails: the shared
    # config keeps the one it had, and no later rebuild brings the other back.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    _serve_catalog(
        monkeypatch,
        process_services,
        AsyncMock(return_value=_runnable_catalog("first")),
    )
    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services
    )
    await _open_catalog(adapter)
    orchestrator = adapter._context.config_orchestrator
    builder = orchestrator._builder
    build = builder.build

    async def build_then_fail(**kwargs: Any) -> Any:
        await build(**kwargs)
        raise ValueError("config rejected")

    monkeypatch.setattr(builder, "build", build_then_fail)
    assert not await adapter._install_model_catalog(_runnable_catalog("second"))
    monkeypatch.setattr(builder, "build", build)

    await orchestrator.arebuild()

    assert "first" in orchestrator.config.models
    assert "second" not in orchestrator.config.models


@pytest.mark.asyncio
async def test_unified_a_catalog_loaded_after_a_refusal_is_not_pushed_again_on_a_pick(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # Refused for the pin, then a newer catalog that keeps the pinned model
    # loads on its own: a later pick has nothing left to load.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._session_model import set_session_active_model_override

    _serve_catalog(
        monkeypatch,
        process_services,
        AsyncMock(
            side_effect=[
                _runnable_catalog("picked-model", "other-model"),
                _runnable_catalog("other-model"),
                _runnable_catalog("picked-model", "other-model", "new-model"),
            ]
        ),
    )
    session = _RecordingSession()
    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services, session, active_model=""
    )
    await _open_catalog(adapter)
    orchestrator = adapter._context.config_orchestrator
    assert not await set_session_active_model_override(
        orchestrator, "picked-model", reason="pick"
    )
    await _refresh_held_catalog(process_services)
    await adapter._settle_configuration()
    await _refresh_held_catalog(process_services)
    await adapter._settle_configuration()
    assert "new-model" in _model_aliases(adapter)
    pushed = len(session.applied)

    assert not await set_session_active_model_override(
        orchestrator, "other-model", reason="pick"
    )
    await adapter._load_remote_values()

    assert len(session.applied) == pushed


@pytest.mark.asyncio
async def test_unified_a_session_opened_before_its_account_is_known_gets_its_catalog(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    # A user token whose tenant is discovered by the open's own resolve: the
    # catalog it fetched still loads.
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._model_catalog_service import ModelCatalogKey

    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services
    )
    resolved: list[ModelCatalogKey] = []
    known = ModelCatalogKey.current
    of = ModelCatalogKey.of

    def current(_cls: Any, *args: Any) -> ModelCatalogKey | None:
        return known(*args) if resolved else None

    async def resolve(_cls: Any, *args: Any) -> ModelCatalogKey | None:
        key = await of(*args)
        if key is not None:
            resolved.append(key)
        return key

    monkeypatch.setattr(ModelCatalogKey, "current", classmethod(current))
    monkeypatch.setattr(ModelCatalogKey, "of", classmethod(resolve))
    _serve_catalog(
        monkeypatch, process_services, AsyncMock(return_value=_runnable_catalog("m"))
    )

    await _open_catalog(adapter)

    assert resolved
    assert "m" in _model_aliases(adapter)


@pytest.mark.asyncio
async def test_unified_a_retired_pick_is_announced_once_across_refreshes(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    process_services: ProcessServices,
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._session_model import set_session_active_model_override

    _serve_catalog(
        monkeypatch,
        process_services,
        AsyncMock(
            side_effect=[
                _runnable_catalog("picked-model", "other-model"),
                _runnable_catalog("other-model"),
                _runnable_catalog("other-model", "new-model"),
            ]
        ),
    )
    session = _RecordingSession()
    adapter = await _catalog_adapter(
        tmp_path, config_dir, monkeypatch, process_services, session, active_model=""
    )
    await _open_catalog(adapter)
    assert not await set_session_active_model_override(
        adapter._context.config_orchestrator, "picked-model", reason="pick"
    )

    for _ in range(2):
        await _refresh_held_catalog(process_services)
        await adapter._settle_configuration()

    assert sum("is no longer" in notice for notice in session.notices) == 1
