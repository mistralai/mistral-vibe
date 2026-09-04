from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tomllib
from typing import TYPE_CHECKING, Any, cast

import pytest
import tomli_w

from vibe.app_server.models import AccountPlanKind
from vibe.app_server.protocol import ClientInfo, SessionOptions
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
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

    def __init__(self, *, active_turn_id: str | None = None) -> None:
        self.active_turn_id = active_turn_id
        self.applied: list[object] = []
        self.settings: list[object] = []
        self.capabilities: list[object] = []
        self.shut_down = False

    async def apply_runtime_configuration(
        self,
        settings: object,
        adapter_config: object,
        capabilities: object,
        *,
        plugins: object = None,
    ) -> None:
        del plugins
        self.settings.append(settings)
        self.applied.append(adapter_config)
        self.capabilities.append(capabilities)

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

    process = HarnessProcess(experimental_harness=True)
    built = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    client = _StubEvalClient(response)
    context = replace(built, experiment_manager=ExperimentManager(client=client))
    _stub_identity_and_whoami(monkeypatch, context)
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session),
        str(tmp_path),
        context,
        context.derive(UnifiedSessionSettings()),
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
    await adapter._flush_pending_derivation()

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

    reopened = await HarnessProcess(
        experimental_harness=True
    ).build_unified_session_context(SessionOptions(cwd=str(tmp_path)))

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
