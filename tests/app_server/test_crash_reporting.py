from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
import tomllib
from types import SimpleNamespace
from typing import Any, cast

import pytest
import tomli_w

from vibe.app_server import server as server_module
from vibe.app_server._agent_socket import session_open_error
from vibe.app_server._completion_facts import ModelFactsError
from vibe.app_server._host import HostRequestHandler
from vibe.app_server._process_services import ProcessServices
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server._session_backend_port import (
    SessionBackend,
    SessionBackendError,
    SessionBackendEvent,
    SessionEventSubscription,
)
from vibe.app_server.client import AppServerClient
from vibe.app_server.models import IdleSessionStatus, PublicSession, PublicSessionState
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientInfo,
    ProtocolErrorCode,
    SessionReadResponse,
)
from vibe.app_server.run_export import RunOutcome
from vibe.app_server.server import AppServer
from vibe.app_server.transport import memory_transport_pair
from vibe.utils import AgentEntrypoint


@dataclass
class _Reports:
    inits: list[bool] = field(default_factory=list)
    captures: list[dict[str, Any]] = field(default_factory=list)


@pytest.fixture
def reports(monkeypatch: pytest.MonkeyPatch) -> _Reports:
    recorded = _Reports()
    monkeypatch.setattr(
        server_module,
        "init_cli_sentry",
        lambda **kwargs: recorded.inits.append(kwargs["enabled"]),
    )
    monkeypatch.setattr(
        server_module,
        "capture_sentry_exception",
        lambda exc, **kwargs: recorded.captures.append(kwargs),
    )
    monkeypatch.setattr(
        server_module, "report_unhandled_asyncio_errors", lambda loop: None
    )
    _hand_dsn(monkeypatch, handed=True)
    return recorded


def _hand_dsn(monkeypatch: pytest.MonkeyPatch, *, handed: bool) -> None:
    monkeypatch.setattr(server_module, "cli_crash_reporting_available", lambda: handed)


@pytest.mark.parametrize(
    ("entrypoint", "dsn_handed", "enable_telemetry", "expected"),
    [
        ("cli", True, True, [True]),
        ("programmatic", True, False, [False]),
        # Desktop, run servers and vibe-acp: no terminal UI launched the process.
        ("desktop", False, True, []),
    ],
)
@pytest.mark.asyncio
async def test_the_user_config_gates_crash_reporting_once_initialized(
    entrypoint: AgentEntrypoint,
    dsn_handed: bool,
    enable_telemetry: bool,
    expected: list[bool],
    config_dir: Path,
    process_services: ProcessServices,
    reports: _Reports,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hand_dsn(monkeypatch, handed=dsn_handed)
    _set_telemetry(config_dir, enabled=enable_telemetry)

    async with _connect(entrypoint, process_services) as (_, server):
        await _settle(server)

    assert reports.inits == expected


@pytest.mark.asyncio
async def test_the_attached_session_config_has_the_last_word(
    config_dir: Path, process_services: ProcessServices, reports: _Reports
) -> None:
    _set_telemetry(config_dir, enabled=True)

    async with _connect("cli", process_services) as (_, server):
        await server._activate_lifecycle_backend(
            "session/start", cast(SessionBackend, _Root(enable_telemetry=False)), 1
        )
        await _settle(server)

    assert reports.inits == [True, False]


@pytest.mark.asyncio
async def test_closing_cancels_every_pending_gate(
    config_dir: Path,
    process_services: ProcessServices,
    reports: _Reports,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = asyncio.Event()

    async def slow_read(self: HostRequestHandler) -> bool:
        await release.wait()
        return True

    monkeypatch.setattr(HostRequestHandler, "telemetry_enabled", slow_read)

    async with _connect("cli", process_services) as (_, server):
        startup_gate = server._crash_reporting_task
        await server._activate_lifecycle_backend(
            "session/start", cast(SessionBackend, _Root(enable_telemetry=False)), 1
        )
        await server.close()
        release.set()

    assert startup_gate is not None and startup_gate.cancelled()
    assert reports.inits == []


@pytest.mark.parametrize(
    ("error", "fingerprint"),
    [
        (
            lambda: _harness_error("replay_divergence"),
            ["app-server-harness-error", "session/resume", "replay_divergence"],
        ),
        (lambda: SessionBackendError(ProtocolErrorCode.INTERNAL_ERROR, "boom"), None),
    ],
)
@pytest.mark.asyncio
async def test_a_failed_resume_with_an_internal_error_is_reported(
    error: Callable[[], SessionBackendError],
    fingerprint: list[str] | None,
    process_services: ProcessServices,
    reports: _Reports,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _fail_resume("programmatic", error(), process_services, monkeypatch)

    assert [capture["fingerprint"] for capture in reports.captures] == [fingerprint]
    assert reports.captures[0]["tags"]["app_server_method"] == "session/resume"


@pytest.mark.parametrize(
    "error",
    [
        lambda: _harness_error("store_requires_newer_reader"),
        lambda: _harness_error("session_busy"),
        lambda: session_open_error(RunOutcome.CONFIG_ERROR, "Context window too small"),
        lambda: session_open_error(RunOutcome.ABORTED, "The agent server aborted"),
        lambda: session_open_error(
            RunOutcome.INFRASTRUCTURE_FAILURE, "The sandbox did not start"
        ),
        lambda: ModelFactsError("No completion profile fits this model"),
        lambda: SessionBackendError(ProtocolErrorCode.NOT_FOUND, "Session not found"),
    ],
)
@pytest.mark.asyncio
async def test_a_resume_refused_for_a_user_reason_is_not_reported(
    error: Callable[[], SessionBackendError],
    process_services: ProcessServices,
    reports: _Reports,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _fail_resume("programmatic", error(), process_services, monkeypatch)

    assert reports.captures == []


@pytest.mark.asyncio
async def test_a_server_no_terminal_ui_launched_never_reports(
    process_services: ProcessServices,
    reports: _Reports,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _hand_dsn(monkeypatch, handed=False)

    await _fail_resume("acp", RuntimeError("boom"), process_services, monkeypatch)

    assert reports.captures == []


def _harness_error(code: str) -> SessionBackendError:
    pytest.importorskip("mistralai_vibe_local_harness")
    from mistralai_vibe_local_harness.vibe import HarnessSessionError
    from vibe.app_server._unified_harness_backend_adapter import (
        _raise_session_backend_error,
    )

    with pytest.raises(SessionBackendError) as raised:
        _raise_session_backend_error(HarnessSessionError(code, f"{code} in session 1"))
    return raised.value


async def _fail_resume(
    entrypoint: AgentEntrypoint,
    error: Exception,
    process_services: ProcessServices,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(*_: Any) -> None:
        raise error

    async with _connect(entrypoint, process_services) as (client, _):
        monkeypatch.setattr(AppServer, "_dispatch_request", fail)
        with pytest.raises(AppServerResponseError):
            await client.request(
                "session/resume", {"sessionId": "session-1", "agentConfig": {}}
            )


@asynccontextmanager
async def _connect(
    entrypoint: AgentEntrypoint, process_services: ProcessServices
) -> AsyncIterator[tuple[AppServerClient, AppServer]]:
    client_transport, server_transport = memory_transport_pair()
    server = await create_harness_server(
        server_transport,
        transport_kind="in_process",
        process=HarnessProcess(services=process_services),
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    try:
        await client.initialize(
            ClientInfo(name="vibe_rs", version="3.1.0", entrypoint=entrypoint)
        )
        await client.notify("initialized")
        # Answered in order, so the notification is handled once this returns.
        await client.request("config/schema")
        yield client, server._server
    finally:
        await client.close()
        await client_transport.close()
        await server_transport.close()


async def _settle(server: AppServer) -> None:
    if server._crash_reporting_task is not None:
        await server._crash_reporting_task


def _set_telemetry(config_dir: Path, *, enabled: bool) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text()) | {"enable_telemetry": enabled}
    config_file.write_text(tomli_w.dumps(config))


class _Root:
    session_id = "session-1"

    def __init__(self, *, enable_telemetry: bool) -> None:
        self._enable_telemetry = enable_telemetry

    def runtime_updated_params(self) -> Any:
        config = SimpleNamespace(enable_telemetry=self._enable_telemetry)
        return SimpleNamespace(runtime=SimpleNamespace(config=config))

    async def subscribe(self, params: object) -> SessionEventSubscription:
        async def events() -> AsyncIterator[SessionBackendEvent]:
            if False:
                yield cast(SessionBackendEvent, None)

        return SessionEventSubscription(
            snapshot=SessionReadResponse(
                state=PublicSessionState(
                    event_id=0,
                    session=PublicSession(
                        id=self.session_id,
                        status=IdleSessionStatus(),
                        created_at=1,
                        updated_at=1,
                    ),
                ),
                last_event_id=0,
            ),
            events=events(),
        )

    async def flush_events(self) -> None:
        pass
