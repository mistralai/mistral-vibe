from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.app_server.models import AccountPlanKind
from vibe.core.experiments.client import RemoteEvalClient
from vibe.core.experiments.manager import ExperimentManager
from vibe.core.experiments.models import EvalResponse, ExperimentAttributes
from vibe.core.experiments.session import (
    hydrate_experiments_from_session,
    initialize_experiments,
    resolve_plan_attributes,
)
from vibe.core.identity import IdentityResult
from vibe.core.telemetry.types import LaunchContext, TerminalEmulator
from vibe.setup.auth.whoami import WhoAmIResult


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
        config=_make_config(enable_telemetry=False),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is False
    persist.assert_not_called()


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
            userId="x", entrypoint="cli", agent_version="0", os="darwin"
        ),
    )
    persist = AsyncMock()
    session_logger = MagicMock()
    session_logger.persist_experiments = persist
    manager = ExperimentManager(client=_StubClient(None))

    result = await initialize_experiments(
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
            userId="x", entrypoint="cli", agent_version="0", os="darwin"
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
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is True
    persist.assert_awaited_once()


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
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_identity",
        AsyncMock(
            return_value=IdentityResult.model_validate({
                "id": "user-1",
                "organization": {"id": "org-123", "name": "Acme"},
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
        config=_make_config(),
        manager=manager,
        session_logger=session_logger,
        launch_context=None,
    )

    assert result[0] is True
    assert client.attributes is not None
    assert client.attributes.organizationId == "org-123"
    assert client.attributes.userId == "user-1"


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
async def test_hydrate_returns_false_when_telemetry_disabled() -> None:
    session_logger = MagicMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    session_logger.session_metadata.experiments = response
    manager = ExperimentManager(client=_StubClient(None))

    result = await hydrate_experiments_from_session(
        config=_make_config(enable_telemetry=False),
        manager=manager,
        session_logger=session_logger,
    )

    assert result is False
    assert manager.export_state() is None


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
async def test_hydrate_returns_false_when_experiments_disabled() -> None:
    # Regression: without this gate, a user who flipped experiments.enable
    # to False between sessions would still resume into a hydrated variant.
    session_logger = MagicMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    session_logger.session_metadata.experiments = response
    manager = ExperimentManager(client=_StubClient(None))

    result = await hydrate_experiments_from_session(
        config=_make_config(enable_experiments=False),
        manager=manager,
        session_logger=session_logger,
    )

    assert result is False
    assert manager.export_state() is None


@pytest.mark.asyncio
async def test_hydrate_restores_only_the_sticky_variant_not_attributes() -> None:
    # Resume restores ONLY the frozen variant assignment from meta.json. Plan/org
    # attributes are user-scoped and rebuilt from the whoami/identity cache by
    # resolve_plan_attributes, so hydrate must NOT read them from meta.json.
    session_logger = MagicMock()
    response = EvalResponse.model_validate({
        "features": {"vibe_cli_system_prompt": {"defaultValue": "cli"}}
    })
    session_logger.session_metadata.experiments = response
    manager = ExperimentManager(client=_StubClient(None))

    result = await hydrate_experiments_from_session(
        config=_make_config(), manager=manager, session_logger=session_logger
    )

    assert result is True
    # Variant assignment restored ...
    assert manager.export_state() is not None
    # ... but attributes are NOT sourced from meta.json.
    assert manager.attributes() is None


@pytest.mark.asyncio
async def test_hydrate_returns_false_when_no_persisted_experiments() -> None:
    session_logger = MagicMock()
    session_logger.session_metadata.experiments = None
    manager = ExperimentManager(client=_StubClient(None))

    result = await hydrate_experiments_from_session(
        config=_make_config(), manager=manager, session_logger=session_logger
    )

    assert result is False
    assert manager.export_state() is None


@pytest.mark.asyncio
async def test_resolve_plan_attributes_fetches_whoami_without_rebucketing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The resume plan-resolution path must NOT re-bucket (never call
    # manager.initialize). It fetches identity + whoami, sets the attribute
    # snapshot + user_plan, and leaves any hydrated EvalResponse intact.
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
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_identity", AsyncMock(return_value=None)
    )
    manager = ExperimentManager(client=_StubClient(None))

    user_plan = await resolve_plan_attributes(
        config=_make_config(), manager=manager, launch_context=None
    )

    assert user_plan == "Code Enterprise"
    attrs = manager.attributes()
    assert attrs is not None
    assert attrs.planType == "mistral_code"
    assert attrs.planName == "E"
    # No re-bucketing — the EvalResponse is untouched.
    assert manager.export_state() is None


@pytest.mark.asyncio
async def test_resolve_plan_attributes_returns_sentinel_when_no_mistral_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _make_config()
    config.get_mistral_provider = lambda: None
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: None,
    )
    manager = ExperimentManager(client=_StubClient(None))

    user_plan = await resolve_plan_attributes(
        config=config, manager=manager, launch_context=None
    )

    assert user_plan == "NO_PLAN_DATA"
    attrs = manager.attributes()
    assert attrs is not None
    assert attrs.planType == "NO_PLAN_DATA"
    assert attrs.planName == "NO_PLAN_DATA"


@pytest.mark.asyncio
async def test_resolve_plan_attributes_null_on_whoami_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mistral key present but whoami fails (returns None): user_plan is null
    # (the failure signal). The snapshot is still stamped (entrypoint/os) with
    # null plan fields.
    monkeypatch.setattr(
        "vibe.core.experiments.session.get_mistral_provider_and_api_key",
        lambda _config: (MagicMock(), "fake-key"),
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_whoami", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(
        "vibe.core.experiments.session.fetch_identity", AsyncMock(return_value=None)
    )
    manager = ExperimentManager(client=_StubClient(None))

    user_plan = await resolve_plan_attributes(
        config=_make_config(), manager=manager, launch_context=None
    )

    assert user_plan is None
    attrs = manager.attributes()
    assert attrs is not None
    assert attrs.planName is None
    assert attrs.planType is None
