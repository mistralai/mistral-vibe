from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.app_server.models import AccountPlanKind
from vibe.core.experiments.active import ExperimentSurface
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
from vibe.core.experiments.session import (
    fetch_and_cache_experiments,
    initialize_experiments,
)
from vibe.core.experiments.wait import experiments_eval_failed
from vibe.core.identity import IdentityResult
from vibe.core.paths import EXPERIMENT_EVAL_CACHE_FILE
from vibe.core.platform_auth import PlatformCredentialSnapshot, PlatformCredentialTarget
from vibe.core.telemetry.types import LaunchContext, TerminalEmulator
from vibe.setup.auth.whoami import WhoAmIResult, store_cached_whoami


@pytest.fixture(autouse=True)
def _stub_fetch_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_identity", AsyncMock(return_value=None)
    )


@pytest.fixture(autouse=True)
def _stub_fetch_whoami(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_whoami", AsyncMock(return_value=None)
    )


class _StubClient(RemoteEvalClient):
    def __init__(self, response: EvalResponse | None) -> None:
        self._response = response
        self.attributes: ExperimentAttributes | None = None

    async def evaluate(self, attributes: ExperimentAttributes) -> EvalResponse | None:
        self.attributes = attributes
        return self._response

    async def aclose(self) -> None:
        pass


def _make_config(
    *, enable_telemetry: bool = True, enable_experiments: bool = True
) -> Any:
    config = MagicMock()
    config.enable_telemetry = enable_telemetry
    config.experiments.enable = enable_experiments
    return config


@pytest.mark.asyncio
async def test_initialize_returns_false_when_telemetry_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    manager = ExperimentManager(client=_StubClient(None))

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(enable_telemetry=False),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is False
    persist.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enable_telemetry", "enable_experiments"), [(False, True), (True, False)]
)
async def test_initialize_clears_eval_cache_when_opted_out(
    monkeypatch: pytest.MonkeyPatch, enable_telemetry: bool, enable_experiments: bool
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    cache_path = EXPERIMENT_EVAL_CACHE_FILE.path
    cache_path.write_text('{"key": {"stored_at_timestamp": 1, "payload": {}}}')

    await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(
            enable_telemetry=enable_telemetry, enable_experiments=enable_experiments
        ),
        manager=ExperimentManager(client=_StubClient(None)),
        session_logger=MagicMock(persist_experiments=AsyncMock()),
        launch_context=None,
    )

    assert not cache_path.exists()


@pytest.mark.asyncio
async def test_initialize_fetches_telemetry_but_skips_growthbook_when_experiments_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `experiments.enable=False` is the A/B opt-out, NOT a telemetry opt-out.
    # With telemetry on we still fetch /whoami + identity and populate the
    # attribute snapshot + user_plan for segmentation — we only avoid the
    # GrowthBook eval (no bucketing, no eval cache, no prompt refresh, no
    # persisted eval state).
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_whoami",
        AsyncMock(
            return_value=WhoAmIResult(
                plan_type=AccountPlanKind.CHAT,
                plan_name="INDIVIDUAL",
                customer_id="cust-1",
                prompt_switching_to_pro_plan=False,
            )
        ),
    )
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    client = _StubClient(None)
    manager = ExperimentManager(client=client)

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(enable_experiments=False),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    # Not "updated" (no eval), but user_plan is populated for telemetry.
    assert result[0] is False
    assert result[1] == "Pro"
    # Attributes were captured for telemetry segmentation ...
    attrs = manager.attributes()
    assert attrs is not None
    assert attrs.planName == "INDIVIDUAL"
    assert attrs.customerId == "cust-1"
    # ... but the GrowthBook eval was NOT called, and no eval state persisted.
    assert client.attributes is None
    assert manager.export_state() is None
    persist.assert_not_called()


@pytest.mark.asyncio
async def test_initialize_returns_no_plan_data_when_no_mistral_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No Mistral provider configured at all — not our user. Expected absence,
    # so the NO_PLAN_DATA sentinel is returned (distinct from null). The active
    # backend is irrelevant to this: the gate is credential presence.
    config = _make_config()
    config.get_mistral_provider = lambda: None
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: None,
    )
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    manager = ExperimentManager(client=_StubClient(None))

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=config,
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is False
    assert result[1] == "NO_PLAN_DATA"
    persist.assert_not_called()
    # The sentinel must also land on the attribute snapshot so telemetry's
    # experiment_attributes carries planType/planName, not null.
    attrs = manager.attributes()
    assert attrs is not None
    assert attrs.planType == "NO_PLAN_DATA"
    assert attrs.planName == "NO_PLAN_DATA"


@pytest.mark.asyncio
async def test_initialize_returns_none_when_mistral_provider_but_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A Mistral provider is configured but no API key resolvable — "tried but
    # failed", not "not our user", so user_plan is None (not the sentinel) and
    # no attribute snapshot is stamped.
    config = _make_config()
    config.get_mistral_provider = lambda: MagicMock()
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: None,
    )
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    manager = ExperimentManager(client=_StubClient(None))

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=config,
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is False
    assert result[1] is None
    persist.assert_not_called()
    assert manager.attributes() is None


@pytest.mark.asyncio
async def test_initialize_fetches_plan_when_mistral_key_but_non_mistral_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The whole point of the change: a third-party active model that still has
    # a Mistral provider/key must report its REAL plan for telemetry, not the
    # NO_PLAN_DATA sentinel. The active backend gates only the UI, never the
    # fetch — so `is_active_model_mistral` is not even consulted here.
    config = _make_config()
    config.is_active_model_mistral = lambda: False
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_whoami",
        AsyncMock(
            return_value=WhoAmIResult(
                plan_type=AccountPlanKind.CHAT,
                plan_name="INDIVIDUAL",
                organization_kind="S",
                customer_id="cust-1",
                prompt_switching_to_pro_plan=False,
            )
        ),
    )
    session_logger = MagicMock()
    session_logger.persist_experiments = AsyncMock()
    manager = ExperimentManager(client=_StubClient(None))

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=config,
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[1] == "Pro"
    attrs = manager.attributes()
    assert attrs is not None
    assert attrs.planName == "INDIVIDUAL"
    assert attrs.planType == "chat"
    assert attrs.customerId == "cust-1"


@pytest.mark.asyncio
async def test_initialize_returns_false_when_remote_eval_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: even if telemetry is enabled and a Mistral key is set,
    # a failed remote eval (returns None) leaves manager state empty —
    # the helper must NOT report success, so the caller skips the
    # unnecessary system prompt refresh.
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session._build_attributes",
        lambda *_args, **_kwargs: ExperimentAttributes(
            userId="x",
            entrypoint="cli",
            harness=ExperimentSurface.LEGACY,
            agent_version="0",
            os="darwin",
        ),
    )
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    manager = ExperimentManager(client=_StubClient(None))

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is False
    # whoami stub returns None (autouse fixture), so user_plan is None —
    # this is the "tried but failed" case, distinct from "NO_PLAN_DATA".
    assert result[1] is None
    persist.assert_not_called()
    assert experiments_eval_failed() is True


@pytest.mark.asyncio
async def test_initialize_returns_true_and_persists_when_remote_eval_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session._build_attributes",
        lambda *_args, **_kwargs: ExperimentAttributes(
            userId="x",
            entrypoint="cli",
            harness=ExperimentSurface.LEGACY,
            agent_version="0",
            os="darwin",
        ),
    )
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    manager = ExperimentManager(client=_StubClient(response))

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is True
    persist.assert_awaited_once()
    assert experiments_eval_failed() is False


@pytest.mark.asyncio
async def test_an_eval_without_a_user_id_stops_later_waits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # No hash attribute: the response assigns nothing and is not cached, so a
    # later session would wait for another eval that cannot route it either.
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    session_logger = MagicMock()
    session_logger.persist_experiments = AsyncMock()

    result = await initialize_experiments(
        harness=ExperimentSurface.UNIFIED,
        config=_make_config(),
        manager=ExperimentManager(client=_StubClient(response)),
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is True
    assert experiments_eval_failed() is True
    assert not EXPERIMENT_EVAL_CACHE_FILE.path.exists()


@pytest.mark.asyncio
async def test_initialize_uses_provided_terminal_emulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    client = _StubClient(response)
    manager = ExperimentManager(client=client)

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=LaunchContext(
            agent_entrypoint="cli",
            agent_version="1.0.0",
            client_name="vibe_cli",
            client_version="1.0.0",
            terminal_emulator=TerminalEmulator.VSCODE,
        ),
    )

    assert result[0] is True
    assert client.attributes is not None
    assert client.attributes.terminal_emulator is TerminalEmulator.VSCODE


@pytest.mark.asyncio
async def test_initialize_includes_organization_id_from_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = MagicMock(
        api_base="https://api.mistral.ai/v1", api_key_env_var="MISTRAL_API_KEY"
    )
    monkeypatch.setenv("MISTRAL_API_KEY", "fake-key")
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (provider, "fake-key"),
    )
    resolve_identity = AsyncMock(
        return_value=IdentityResult.model_validate({
            "id": "user-1",
            "organization": {"id": "org-123", "name": "Acme"},
        })
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_identity", resolve_identity
    )
    session_logger = MagicMock()
    session_logger.persist_experiments = AsyncMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    client = _StubClient(response)
    manager = ExperimentManager(client=client)

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is True
    assert client.attributes is not None
    assert client.attributes.organizationId == "org-123"
    assert client.attributes.userId == "user-1"
    resolve_identity.assert_awaited_once()
    identity_call = resolve_identity.await_args
    assert identity_call is not None
    credentials = identity_call.kwargs["credentials"]
    snapshot = await credentials.resolve(
        PlatformCredentialTarget(base_url=provider.api_base)
    )
    assert isinstance(snapshot, PlatformCredentialSnapshot)
    assert snapshot.cache_namespace == "api_key:MISTRAL_API_KEY"


@pytest.mark.asyncio
async def test_initialize_omits_organization_id_when_identity_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Fail-open: identity fetch returns None (stubbed by the autouse fixture),
    # so experiment init still succeeds with organization_id absent.
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    session_logger = MagicMock()
    session_logger.persist_experiments = AsyncMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    client = _StubClient(response)
    manager = ExperimentManager(client=client)

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is True
    assert client.attributes is not None
    assert client.attributes.organizationId is None
    assert client.attributes.userId is None


@pytest.mark.asyncio
async def test_initialize_includes_organization_kind_from_whoami(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_whoami",
        AsyncMock(
            return_value=WhoAmIResult(
                plan_type=AccountPlanKind.MISTRAL_CODE,
                plan_name="E",
                organization_kind="S",
                prompt_switching_to_pro_plan=False,
            )
        ),
    )
    session_logger = MagicMock()
    session_logger.persist_experiments = AsyncMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    client = _StubClient(response)
    manager = ExperimentManager(client=client)

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is True
    assert client.attributes is not None
    assert client.attributes.organizationKind == "S"


@pytest.mark.asyncio
async def test_initialize_includes_plan_from_whoami(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_whoami",
        AsyncMock(
            return_value=WhoAmIResult(
                plan_type=AccountPlanKind.MISTRAL_CODE,
                plan_name="E",
                organization_kind="S",
                prompt_switching_to_pro_plan=False,
            )
        ),
    )
    session_logger = MagicMock()
    session_logger.persist_experiments = AsyncMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    client = _StubClient(response)
    manager = ExperimentManager(client=client)

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is True
    assert client.attributes is not None
    assert client.attributes.planType == "mistral_code"
    assert client.attributes.planName == "E"
    assert result[1] == "Code Enterprise"


@pytest.mark.asyncio
async def test_initialize_omits_plan_when_whoami_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    # _stub_fetch_whoami autouse fixture already returns None — no override needed.
    session_logger = MagicMock()
    session_logger.persist_experiments = AsyncMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    client = _StubClient(response)
    manager = ExperimentManager(client=client)

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is True
    assert client.attributes is not None
    assert client.attributes.planType is None
    assert client.attributes.planName is None


@pytest.mark.asyncio
async def test_initialize_omits_organization_kind_when_whoami_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    # _stub_fetch_whoami autouse fixture already returns None — no override needed.
    session_logger = MagicMock()
    session_logger.persist_experiments = AsyncMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    client = _StubClient(response)
    manager = ExperimentManager(client=client)

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is True
    assert client.attributes is not None
    assert client.attributes.organizationKind is None


@pytest.mark.asyncio
async def test_initialize_includes_workspace_id_from_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_identity",
        AsyncMock(
            return_value=IdentityResult.model_validate({
                "id": "user-1",
                "workspace": {"id": "ws-456", "name": "My Workspace"},
            })
        ),
    )
    session_logger = MagicMock()
    session_logger.persist_experiments = AsyncMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    client = _StubClient(response)
    manager = ExperimentManager(client=client)

    result = await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is True
    assert client.attributes is not None
    assert client.attributes.workspaceId == "ws-456"


@pytest.mark.asyncio
async def test_initialize_uses_resolve_whoami_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    whoami_result = WhoAmIResult(
        plan_type=AccountPlanKind.MISTRAL_CODE,
        plan_name="E",
        organization_kind="P",
        prompt_switching_to_pro_plan=False,
    )
    resolve_whoami = AsyncMock(return_value=whoami_result)
    session_logger = MagicMock()
    session_logger.persist_experiments = AsyncMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    client = _StubClient(response)
    manager = ExperimentManager(client=client)

    await initialize_experiments(
        harness=ExperimentSurface.LEGACY,
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
        resolve_whoami=resolve_whoami,
    )

    resolve_whoami.assert_awaited_once()
    assert client.attributes is not None
    assert client.attributes.organizationKind == "P"


@pytest.mark.asyncio
@pytest.mark.parametrize("harness", list(ExperimentSurface))
async def test_initialize_reports_the_serving_backend_to_growthbook(
    monkeypatch: pytest.MonkeyPatch, harness: ExperimentSurface
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    client = _StubClient(EvalResponse.model_validate({"features": {}}))
    manager = ExperimentManager(client=client)

    await initialize_experiments(
        harness=harness,
        config=_make_config(),
        manager=manager,
        session_logger=MagicMock(persist_experiments=AsyncMock()),
        launch_context=None,
    )

    assert client.attributes is not None
    assert client.attributes.harness is harness


@pytest.mark.asyncio
async def test_initialize_caches_the_eval_response_once_bucketing_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session._build_attributes",
        lambda *_args, **_kwargs: ExperimentAttributes(
            userId="user-1",
            entrypoint="cli",
            harness=ExperimentSurface.UNIFIED,
            agent_version="0",
            os="darwin",
        ),
    )
    stored: list[EvalResponse] = []
    monkeypatch.setattr(
        "vibe.core.experiments.session.store_cached_eval_response",
        lambda _config, response: stored.append(response),
    )
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    manager = ExperimentManager(client=_StubClient(response))

    await initialize_experiments(
        harness=ExperimentSurface.UNIFIED,
        config=_make_config(),
        manager=manager,
        session_logger=MagicMock(persist_experiments=AsyncMock()),
        launch_context=None,
    )

    assert stored == [response]


@pytest.mark.asyncio
async def test_initialize_keeps_the_cache_when_bucketing_never_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Without the hash attribute the response carries no assignment, and caching
    # it would overwrite a good entry.
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session._build_attributes",
        lambda *_args, **_kwargs: ExperimentAttributes(
            userId=None,
            entrypoint="cli",
            harness=ExperimentSurface.UNIFIED,
            agent_version="0",
            os="darwin",
        ),
    )
    stored: list[EvalResponse] = []
    monkeypatch.setattr(
        "vibe.core.experiments.session.store_cached_eval_response",
        lambda _config, response: stored.append(response),
    )
    manager = ExperimentManager(
        client=_StubClient(EvalResponse.model_validate({"features": {}}))
    )

    await initialize_experiments(
        harness=ExperimentSurface.UNIFIED,
        config=_make_config(),
        manager=manager,
        session_logger=MagicMock(persist_experiments=AsyncMock()),
        launch_context=None,
    )

    assert stored == []


def _launch_context() -> LaunchContext:
    return LaunchContext(
        agent_entrypoint="cli",
        agent_version="0",
        client_name="vibe-desktop",
        client_version="0",
    )


@pytest.mark.asyncio
async def test_fetch_and_cache_takes_the_plan_from_the_whoami_disk_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # As on a session start: the cached /whoami answer, not a live call.
    monkeypatch.setenv("MISTRAL_API_KEY", "fake-key")
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (
            MagicMock(
                api_base="https://api.mistral.ai/v1", api_key_env_var="MISTRAL_API_KEY"
            ),
            "fake-key",
        ),
    )
    monkeypatch.setattr(
        "vibe.core.identity_cache.fetch_identity", AsyncMock(return_value=None)
    )
    live_whoami = AsyncMock(side_effect=AssertionError("live /whoami call"))
    monkeypatch.setattr("vibe.setup.auth.whoami.HttpAccountGateway.read", live_whoami)
    store_cached_whoami(
        "fake-key",
        WhoAmIResult(
            plan_type=AccountPlanKind.MISTRAL_CODE,
            plan_name="cached-plan",
            prompt_switching_to_pro_plan=False,
        ),
    )
    client = _StubClient(None)
    monkeypatch.setattr(
        RemoteEvalClient, "from_settings", classmethod(lambda cls, **_: client)
    )

    await fetch_and_cache_experiments(
        _make_config(), _launch_context(), harness=ExperimentSurface.UNIFIED
    )

    assert client.attributes is not None
    assert client.attributes.planName == "cached-plan"
    live_whoami.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_and_cache_never_raises_and_records_the_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.experiments.session.initialize_experiments",
        AsyncMock(side_effect=RuntimeError("eval exploded")),
    )
    monkeypatch.setattr(
        RemoteEvalClient,
        "from_settings",
        classmethod(lambda cls, **_: _StubClient(None)),
    )

    await fetch_and_cache_experiments(
        _make_config(), _launch_context(), harness=ExperimentSurface.UNIFIED
    )

    assert experiments_eval_failed() is True
