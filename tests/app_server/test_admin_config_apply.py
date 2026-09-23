from __future__ import annotations

import asyncio
from typing import Any

import pytest

from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_mcp_registry import FakeMCPRegistry
from vibe.app_server import _admin_config
from vibe.app_server._execution import SessionExecution
from vibe.app_server._resources import ResourceRequestHandler
from vibe.app_server.protocol import ConfigReloadParams
from vibe.core.agent_loop import AgentLoop
from vibe.core.config.admin_config import ManagedConfig, ManagedConfigResult
from vibe.core.config.default_orchestrator import build_default_orchestrator


async def _build_handler(monkeypatch) -> ResourceRequestHandler:
    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: "api-key")
    orchestrator = await build_default_orchestrator()
    loop = AgentLoop(
        config_orchestrator=orchestrator,
        agent_name="ask",
        backend=FakeBackend(),
        mcp_registry=FakeMCPRegistry(),
    )

    async def notify(method, payload):
        return None

    return ResourceRequestHandler(loop, SessionExecution(), notify)


def _admin_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in events if e["event_name"] == "vibe.admin_config_applied"]


@pytest.mark.asyncio
async def test_apply_admin_config_updates_effective_config(
    monkeypatch, telemetry_events: list[dict[str, Any]]
) -> None:
    async def fake_fetch(base_url, api_key):
        return ManagedConfigResult(
            config=ManagedConfig(state="enabled", toml='theme = "nord"\n')
        )

    monkeypatch.setattr(_admin_config, "fetch_managed_config", fake_fetch)
    handler = await _build_handler(monkeypatch)

    assert handler._agent_loop.config.theme != "nord"
    changed = await handler.apply_admin_config()
    assert changed is True
    assert handler._agent_loop.config.theme == "nord"

    admin = _admin_events(telemetry_events)
    assert len(admin) == 1
    props = admin[0]["properties"]
    assert props["outcome"] == "applied"
    assert props["nb_enforced_fields"] == 1
    assert "enforced_keys" not in props
    assert "has_error" not in props


@pytest.mark.asyncio
async def test_apply_admin_config_no_api_key_emits_no_telemetry(
    monkeypatch, telemetry_events: list[dict[str, Any]]
) -> None:
    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: None)
    orchestrator = await build_default_orchestrator()
    loop = AgentLoop(
        config_orchestrator=orchestrator,
        agent_name="ask",
        backend=FakeBackend(),
        mcp_registry=FakeMCPRegistry(),
    )

    async def notify(method, payload):
        return None

    handler = ResourceRequestHandler(loop, SessionExecution(), notify)

    changed = await handler.apply_admin_config()
    assert changed is False
    assert _admin_events(telemetry_events) == []


@pytest.mark.asyncio
async def test_apply_admin_config_reports_fetch_failure(
    monkeypatch,
    telemetry_events: list[dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fake_fetch(base_url, api_key):
        return ManagedConfigResult(error="HTTP 503")

    monkeypatch.setattr(_admin_config, "fetch_managed_config", fake_fetch)
    handler = await _build_handler(monkeypatch)

    with caplog.at_level("WARNING"):
        changed = await handler.apply_admin_config()
    assert changed is False
    admin = _admin_events(telemetry_events)
    assert len(admin) == 1
    props = admin[0]["properties"]
    assert props["outcome"] == "fetch_failed"
    assert props["has_error"] is True
    assert any(
        "Admin-managed config not applied" in record.message
        and "HTTP 503" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_config_reload_reports_admin_fetch_failure(
    monkeypatch,
    telemetry_events: list[dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fake_fetch(base_url, api_key):
        return ManagedConfigResult(error="HTTP 503")

    monkeypatch.setattr(_admin_config, "fetch_managed_config", fake_fetch)
    handler = await _build_handler(monkeypatch)

    with caplog.at_level("WARNING"):
        await handler._config_reload(
            ConfigReloadParams(
                session_id=handler._agent_loop.session_id, reload_runtime=False
            )
        )

    admin = _admin_events(telemetry_events)
    assert len(admin) == 1
    assert admin[0]["properties"]["outcome"] == "fetch_failed"
    assert any(
        "Admin-managed config not applied" in record.message
        and "HTTP 503" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_apply_admin_config_invalid_toml_rolls_back(
    monkeypatch,
    telemetry_events: list[dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fake_fetch(base_url, api_key):
        # Parseable TOML, but active_model must be a string: fails validation.
        return ManagedConfigResult(
            config=ManagedConfig(state="enabled", toml="active_model = 123\n")
        )

    monkeypatch.setattr(_admin_config, "fetch_managed_config", fake_fetch)
    handler = await _build_handler(monkeypatch)
    baseline = handler._agent_loop.config.active_model

    with caplog.at_level("WARNING"):
        changed = await handler.apply_admin_config()

    assert changed is False
    assert handler._agent_loop.config.active_model == baseline

    # The invalid data was rolled back, so later reloads no longer re-fail.
    await handler._agent_loop.config_orchestrator.reload()
    assert handler._agent_loop.config.active_model == baseline

    admin = _admin_events(telemetry_events)
    assert len(admin) == 1
    assert admin[0]["properties"]["outcome"] == "apply_failed"
    assert admin[0]["properties"]["has_error"] is True


@pytest.mark.asyncio
async def test_apply_admin_config_unmatched_allowed_models_rolls_back(
    monkeypatch, telemetry_events: list[dict[str, Any]]
) -> None:
    async def fake_fetch(base_url, api_key):
        return ManagedConfigResult(
            config=ManagedConfig(
                state="enabled", toml='allowed_models = ["missing-model"]\n'
            )
        )

    monkeypatch.setattr(_admin_config, "fetch_managed_config", fake_fetch)
    handler = await _build_handler(monkeypatch)
    baseline_models = set(handler._agent_loop.config.available_models())
    baseline_active = handler._agent_loop.config.get_active_model().alias

    changed = await handler.apply_admin_config()

    assert changed is False
    assert set(handler._agent_loop.config.available_models()) == baseline_models
    assert handler._agent_loop.config.get_active_model().alias == baseline_active

    # The unmatched policy was rolled back, so later reloads stay projectable.
    await handler._agent_loop.config_orchestrator.reload()
    assert handler._agent_loop.config.get_active_model().alias == baseline_active

    admin = _admin_events(telemetry_events)
    assert len(admin) == 1
    assert admin[0]["properties"]["outcome"] == "apply_failed"
    assert admin[0]["properties"]["has_error"] is True


@pytest.mark.asyncio
async def test_apply_admin_config_rolls_back_when_the_preflight_rejects_it(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Config the caller cannot build a session from must not survive the apply:
    # left live it would re-break every later reload and config edit for the
    # session, none of which can undo a layer the orchestrator owns.

    async def fake_fetch(base_url, api_key):
        return ManagedConfigResult(
            config=ManagedConfig(state="enabled", toml='theme = "nord"\n')
        )

    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: "api-key")
    monkeypatch.setattr(_admin_config, "fetch_managed_config", fake_fetch)
    orchestrator = await build_default_orchestrator()
    baseline = orchestrator.config.theme

    async def rejecting_preflight(_candidate) -> None:
        raise RuntimeError("the Core cannot be built from this")

    async def unreachable_apply() -> bool:
        raise AssertionError("a rejected config must never be pushed live")

    with caplog.at_level("WARNING"):
        changed = await _admin_config.apply_admin_config(
            orchestrator, apply=unreachable_apply, preflight=rejecting_preflight
        )

    assert changed is False
    assert orchestrator.config.theme == baseline
    await orchestrator.reload()
    assert orchestrator.config.theme == baseline


@pytest.mark.asyncio
async def test_load_admin_layer_restores_and_reraises_when_cancelled(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    # Shutdown and a superseded refresh await the cancellation itself, so it
    # must propagate instead of becoming an apply failure whose rollback
    # reload keeps reconfiguring a session that is being torn down.
    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: "api-key")
    orchestrator = await build_default_orchestrator()
    baseline = orchestrator.config.theme

    async def cancelling_preflight(_candidate) -> None:
        raise asyncio.CancelledError()

    with caplog.at_level("WARNING"), pytest.raises(asyncio.CancelledError):
        await _admin_config.load_admin_layer(
            orchestrator, 'theme = "nord"\n', preflight=cancelling_preflight
        )

    assert not any(
        "Admin-managed config failed validation" in record.message
        for record in caplog.records
    )
    await orchestrator.reload()
    assert orchestrator.config.theme == baseline


@pytest.mark.asyncio
async def test_apply_admin_config_times_out_rather_than_waiting_on_the_endpoint(
    monkeypatch, telemetry_events: list[dict[str, Any]]
) -> None:
    # A slow endpoint must not hold up whoever is waiting on the refresh.
    slept = asyncio.Event()

    async def hanging_fetch(base_url, api_key):
        slept.set()
        await asyncio.sleep(3600)

    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: "api-key")
    monkeypatch.setattr(_admin_config, "fetch_managed_config", hanging_fetch)
    orchestrator = await build_default_orchestrator()

    async def unreachable_apply() -> bool:
        raise AssertionError("a config that never arrived cannot be applied")

    changed = await _admin_config.apply_admin_config(
        orchestrator, apply=unreachable_apply, timeout=0.05
    )

    assert slept.is_set()
    assert changed is False


@pytest.mark.asyncio
async def test_apply_admin_config_disabled_emits_no_telemetry(
    monkeypatch,
    telemetry_events: list[dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fake_fetch(base_url, api_key):
        return ManagedConfigResult(config=ManagedConfig(state="disabled", toml=None))

    monkeypatch.setattr(_admin_config, "fetch_managed_config", fake_fetch)
    handler = await _build_handler(monkeypatch)

    with caplog.at_level("WARNING"):
        changed = await handler.apply_admin_config()
    assert changed is False
    assert _admin_events(telemetry_events) == []
    assert not any(
        "Admin-managed config not applied" in record.message
        for record in caplog.records
    )
