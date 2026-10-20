from __future__ import annotations

import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tomllib
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
import tomli_w

from tests.stubs.experiment_responses import routing_eval_response
from vibe.app_server.models import AccountPlanKind
from vibe.app_server.protocol import ClientInfo, SessionOptions
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
from vibe.core.experiments.wait import experiments_eval_failed
from vibe.core.identity import IdentityResult
from vibe.core.telemetry.types import LaunchContext
from vibe.setup.auth.whoami import WhoAmIResult

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
    cwd: str | None = None

    def __init__(self, *, active_turn_id: str | None = None) -> None:
        self.active_turn_id = active_turn_id
        self.applied: list[object] = []
        self.settings: list[object] = []
        self.capabilities: list[object] = []
        self.system_instructions: list[str | None] = []
        self.shut_down = False

    def configure_turn_settlement(self, settle: object) -> None:
        """Held, not called: this double admits no turns."""
        self.settle_configuration = settle

    async def apply_config(
        self, configuration: Any, *, allow_reserved_turn: bool = False
    ) -> None:
        del allow_reserved_turn
        self.system_instructions.append(configuration.core.system_instructions)
        self.settings.append(configuration.core.settings)
        self.applied.append(configuration.local)
        self.capabilities.append(configuration.core.capabilities)

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
def _enable_experiments(config_dir: Path, *, enable: bool = True) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["enable_telemetry"] = True
    config["experiments"] = {"enable": enable}
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


def _stub_identity_and_whoami(
    monkeypatch: pytest.MonkeyPatch, context: UnifiedSessionContext
) -> None:
    monkeypatch.setattr(
        "vibe.core.identity_cache.fetch_identity",
        _async_return(IdentityResult(id="user-1", organization=None, workspace=None)),
    )
    context.whoami_cache.populate(
        base_url=context.config_orchestrator.config.console_base_url,
        api_key="mock",
        result=WhoAmIResult(
            plan_type=AccountPlanKind.CHAT,
            plan_name="INDIVIDUAL",
            customer_id="cust-1",
            prompt_switching_to_pro_plan=False,
        ),
    )


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
) -> tuple[Any, _StubEvalClient]:
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )

    process = HarnessProcess()
    built = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    client = _StubEvalClient(response)
    context = replace(built, experiment_manager=ExperimentManager(client=client))
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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)
    session = _RecordingSession()
    adapter, _client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=session,
        monkeypatch=monkeypatch,
    )
    assert _ROUTED_ALIAS not in _model_aliases(adapter)

    await adapter.initialize_experiments(_launch_context())

    config = adapter._context.config_orchestrator.config
    assert _ROUTED_ALIAS in config.available_models()
    assert _ROUTED_ALIAS in _model_aliases(adapter)
    assert session.applied, "the derivation was never pushed into the live session"


@pytest.mark.asyncio
async def test_unified_experiment_reports_its_surface_to_growthbook(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.core.experiments.active import ExperimentSurface

    _enable_experiments(config_dir)
    adapter, client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
    )

    await adapter.initialize_experiments(_launch_context())

    assert client.attributes is not None
    assert client.attributes.harness is ExperimentSurface.UNIFIED
    assert client.attributes.userId == "user-1"
    assert client.attributes.planName == "INDIVIDUAL"


@pytest.mark.asyncio
async def test_unified_variants_resolved_mid_turn_wait_for_the_next_turn(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)
    session = _RecordingSession(active_turn_id="turn-1")
    adapter, _client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=session,
        monkeypatch=monkeypatch,
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
async def test_unified_experiments_are_skipped_when_the_ab_opt_out_is_set(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir, enable=False)
    session = _RecordingSession()
    adapter, client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=session,
        monkeypatch=monkeypatch,
    )

    await adapter.initialize_experiments(_launch_context())

    assert client.attributes is None
    assert session.applied == []


@pytest.mark.asyncio
async def test_unified_failed_eval_leaves_the_session_as_it_opened(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)
    session = _RecordingSession()
    adapter, _client = await _build_adapter(
        tmp_path, response=None, session=session, monkeypatch=monkeypatch
    )
    opened_with = _model_aliases(adapter)

    await adapter.initialize_experiments(_launch_context())

    assert session.applied == []
    assert _model_aliases(adapter) == opened_with


@pytest.mark.asyncio
async def test_unified_experiment_initialization_never_raises(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)
    session = _RecordingSession()
    adapter, client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=session,
        monkeypatch=monkeypatch,
    )

    async def _explode(_attributes: ExperimentAttributes) -> EvalResponse | None:
        raise RuntimeError("eval exploded")

    monkeypatch.setattr(client, "evaluate", _explode)

    await adapter.initialize_experiments(_launch_context())
    assert session.applied == []
    assert experiments_eval_failed() is True


@pytest.mark.asyncio
async def test_unified_session_teardown_closes_the_eval_client(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)
    session = _RecordingSession()
    adapter, client = await _build_adapter(
        tmp_path,
        response=_extra_models_response(),
        session=session,
        monkeypatch=monkeypatch,
    )
    await adapter.initialize_experiments(_launch_context())

    await adapter.shutdown()

    assert client.closed
    assert session.shut_down


@pytest.mark.asyncio
async def test_unified_resolved_variants_carry_to_the_next_session_through_the_cache(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
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
    )
    await adapter.initialize_experiments(_launch_context())

    reopened = await HarnessProcess().build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )

    assert _ROUTED_ALIAS in reopened.config_orchestrator.config.available_models()


@pytest.mark.asyncio
async def test_the_experiment_task_is_handed_to_the_server_as_a_done_callback(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
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


def test_the_unified_experiment_sink_persists_nothing() -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import asyncio

    from vibe.app_server._unified_harness_backend_adapter import _NULL_EXPERIMENT_SINK
    from vibe.core.experiments.session import ExperimentStateSink

    # The structural fit is what lets one initializer serve both backends, so
    # bind it to the Protocol and let the type checker hold that edge.
    sink: ExperimentStateSink = _NULL_EXPERIMENT_SINK
    assert asyncio.run(sink.persist_experiments(_extra_models_response())) is None


def _runnable_catalog(model_id: str) -> Any:
    from vibe.core.model_catalog import parse_model_catalog_response

    return parse_model_catalog_response({
        "models": [
            {
                "id": model_id,
                "label": model_id,
                "capabilities": {"function_calling": True},
            }
        ]
    })


async def _adapter_fetching(
    tmp_path: Path, session: _RecordingSession, monkeypatch: pytest.MonkeyPatch
) -> tuple[Any, AsyncMock]:
    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    monkeypatch.setattr(
        adapter_module,
        "fetch_and_cache_model_catalog",
        AsyncMock(return_value=_runnable_catalog("fetched-model")),
    )
    adapter, _client = await _build_adapter(
        tmp_path, response=None, session=session, monkeypatch=monkeypatch
    )
    announced = AsyncMock()
    monkeypatch.setattr(adapter, "_announce_runtime", announced)
    monkeypatch.setattr(adapter, "wait_announced", AsyncMock())
    return adapter, announced


@pytest.mark.asyncio
async def test_unified_fetched_catalog_reaches_the_live_model_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession()
    adapter, announced = await _adapter_fetching(tmp_path, session, monkeypatch)

    await adapter._fetch_model_catalog()

    assert "fetched-model" in _model_aliases(adapter)
    assert session.applied, "the derivation was never pushed into the live session"
    announced.assert_awaited_once()


@pytest.mark.asyncio
async def test_unified_catalog_fetched_mid_turn_waits_for_the_next_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession(active_turn_id="turn-1")
    adapter, announced = await _adapter_fetching(tmp_path, session, monkeypatch)

    await adapter._fetch_model_catalog()
    mid_turn_aliases = _model_aliases(adapter)
    session.active_turn_id = None
    await adapter._settle_configuration()

    assert "fetched-model" not in mid_turn_aliases
    assert len(session.applied) == 1
    assert "fetched-model" in _model_aliases(adapter)


@pytest.mark.asyncio
async def test_unified_fetch_returning_the_same_catalog_announces_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    session = _RecordingSession()
    adapter, announced = await _adapter_fetching(tmp_path, session, monkeypatch)
    await adapter._fetch_model_catalog()

    await adapter._fetch_model_catalog()

    assert len(session.applied) == 1
    announced.assert_awaited_once()


@pytest.mark.asyncio
async def test_unified_pin_on_a_catalog_alias_selects_the_fetched_model(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.app_server._unified_harness_backend_adapter as adapter_module
    from vibe.core.model_catalog import parse_model_catalog_response

    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config["active_model"] = "fetched-old"
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")
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
    monkeypatch.setattr(
        adapter_module, "fetch_and_cache_model_catalog", AsyncMock(return_value=catalog)
    )
    adapter, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
    )
    monkeypatch.setattr(adapter, "_announce_runtime", AsyncMock())
    monkeypatch.setattr(adapter, "wait_announced", AsyncMock())

    await adapter._fetch_model_catalog()

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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    _write_user_config(config_dir, active_model="")
    released = asyncio.Event()

    async def _slow_fetch(_config: object) -> object:
        await released.wait()
        return _runnable_catalog("fetched-model")

    monkeypatch.setattr(adapter_module, "fetch_and_cache_model_catalog", _slow_fetch)
    adapter, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    _write_user_config(config_dir)  # the test config pins a built-in model
    released = asyncio.Event()

    async def _slow_fetch(_config: object) -> object:
        await released.wait()
        return None

    monkeypatch.setattr(adapter_module, "fetch_and_cache_model_catalog", _slow_fetch)
    adapter, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.core.model_catalog.session as session_module
    import vibe.core.telemetry.send as telemetry_send

    class _UnreachableClient:
        @classmethod
        def from_provider(cls, _provider: object) -> _UnreachableClient:
            return cls()

        async def fetch(self, _api_key: str) -> None:
            return None

        async def aclose(self) -> None:
            pass

    _write_user_config(config_dir, active_model="")
    monkeypatch.setattr(session_module, "ModelCatalogClient", _UnreachableClient)
    monkeypatch.setattr(
        telemetry_send,
        "get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "test-key"),
    )
    first, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
    )
    first._start_model_catalog_fetch()
    assert first._model_catalog_wait is not None
    await first.dispatch_extension("session/ready/wait", {})

    second, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._session_model import set_session_active_model_override
    import vibe.app_server._unified_harness_backend_adapter as adapter_module

    _write_user_config(config_dir)  # pinned to a built-in model: no wait alone
    released = asyncio.Event()

    async def _slow_fetch(_config: object) -> object:
        await released.wait()
        return _runnable_catalog("fetched-model")

    monkeypatch.setattr(adapter_module, "fetch_and_cache_model_catalog", _slow_fetch)
    adapter, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.app_server._unified_harness_backend_adapter as adapter_module
    import vibe.core.model_catalog.wait as wait_module

    _write_user_config(config_dir, active_model="")
    released = asyncio.Event()

    async def _stuck_fetch(_config: object) -> None:
        await released.wait()

    monkeypatch.setattr(adapter_module, "fetch_and_cache_model_catalog", _stuck_fetch)
    monkeypatch.setattr(wait_module, "MODEL_CATALOG_WAIT_CAP_SECONDS", 0.05)
    first, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
    )
    first._start_model_catalog_fetch()

    result = await asyncio.wait_for(
        first.dispatch_extension("session/ready/wait", {}), timeout=2
    )

    assert result.response.ready is True
    second, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.core.telemetry.send as telemetry_send

    _write_user_config(config_dir, active_model="")
    monkeypatch.setattr(
        telemetry_send, "get_mistral_provider_and_api_key", lambda _config: None
    )
    adapter, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    adapter, client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _enable_experiments(config_dir)  # the test config pins a built-in model
    adapter, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
    )

    _start_experiments(adapter)

    assert adapter._experiments_wait is None
    task = adapter._experiments_task
    assert task is not None
    await task


@pytest.mark.asyncio
async def test_unified_a_reopened_session_with_a_pinned_model_does_not_wait(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    first, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
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
    )
    _start_experiments(second)

    assert second._experiments_wait is None
    second_task = second._experiments_task
    assert second_task is not None
    await second_task


@pytest.mark.asyncio
async def test_unified_sessions_stop_waiting_once_an_eval_failed(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    first, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
    )
    _start_experiments(first)
    assert first._experiments_wait is not None
    await asyncio.wait_for(first._wait_for_first_turn_fetches(), timeout=2)

    second, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
    )
    _start_experiments(second)

    assert second._experiments_wait is None
    for adapter in (first, second):
        task = adapter._experiments_task
        assert task is not None
        await task


@pytest.mark.asyncio
async def test_unified_sessions_stop_waiting_once_an_eval_outlasts_the_cap(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
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
    )
    _hold_eval(client, monkeypatch)
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
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
    )
    _start_experiments(second)
    assert second._experiments_wait is None
    first._cancel_experiments_task()
    await ready
    second_task = second._experiments_task
    assert second_task is not None
    await second_task


@pytest.mark.asyncio
async def test_unified_an_eval_outlasting_the_cap_in_a_pinned_session_stops_later_waits(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
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
    )
    _hold_eval(client, monkeypatch)
    _start_experiments(pinned)
    assert pinned._experiments_wait is None

    await asyncio.sleep(0.2)

    assert experiments_eval_failed() is True
    _unpin_with_experiments(config_dir)
    unpinned, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
    )
    _start_experiments(unpinned)
    assert unpinned._experiments_wait is None
    pinned._cancel_experiments_task()
    task = unpinned._experiments_task
    assert task is not None
    await task


@pytest.mark.asyncio
async def test_unified_a_cache_written_after_the_session_was_built_still_waits(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")

    _unpin_with_experiments(config_dir)
    second, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
    )
    first, _client = await _build_adapter(
        tmp_path,
        response=routing_eval_response(_ROUTED_ALIAS),
        session=_RecordingSession(),
        monkeypatch=monkeypatch,
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
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    import vibe.core.experiments.wait as wait_module

    # The A/B opt-out still resolves identity for telemetry, but sends no eval.
    _enable_experiments(config_dir, enable=False)
    monkeypatch.setattr(wait_module, "EVAL_WAIT_CAP_SECONDS", 0.05)
    adapter, _client = await _build_adapter(
        tmp_path, response=None, session=_RecordingSession(), monkeypatch=monkeypatch
    )
    released = asyncio.Event()

    async def _slow_identity(*_args: object, **_kwargs: object) -> object:
        await released.wait()
        return IdentityResult(id="user-1", organization=None, workspace=None)

    monkeypatch.setattr("vibe.core.identity_cache.fetch_identity", _slow_identity)
    _start_experiments(adapter)

    await asyncio.sleep(0.2)

    assert adapter._experiments_wait is None
    assert experiments_eval_failed() is False
    released.set()
    task = adapter._experiments_task
    assert task is not None
    await task
