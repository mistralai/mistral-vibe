from __future__ import annotations

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
    orchestrator = await build_default_orchestrator(require_api_key=False)
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
    orchestrator = await build_default_orchestrator(require_api_key=False)
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
