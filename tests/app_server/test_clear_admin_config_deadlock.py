"""Regression test: /clear must not deadlock session/ready/wait on the admin
config fetch.

Before the fix, the admin config task awaited ``wait_announced()`` before
sending its ``runtime/updated`` notification.  ``mark_announced()`` is only
called inside ``after_response`` (the server's post-response callback), but
``session/ready/wait`` awaits the admin config task.  If ``after_response`` was
delayed or never fired, both hung forever — a permanent freeze after /clear.

The fix removes the ``wait_announced()`` gate: the server's notification
routing already queues notifications during attachment and flushes them
after the response, so the notification reaches the client regardless of
timing.  ``session/ready/wait`` can now complete as soon as the fetch finishes
(or fails), without depending on ``after_response``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast

import pytest

from tests.stubs.app_server import FakeSessionBackendServices
from vibe.app_server.protocol import (
    SessionHistoryClearParams,
    SessionOptions,
    SessionReadyWaitParams,
    SessionReadyWaitResponse,
    SessionStartParams,
)


@pytest.mark.asyncio
async def test_clear_ready_wait_without_after_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session/ready/wait must complete even if after_response was never called.

    This is the deadlock scenario: after_response calls mark_announced(), and
    the admin config task used to wait for that before sending its notification.
    If after_response never fires, the admin config task hung on
    wait_announced(), and session/ready/wait hung waiting for the admin config
    task.  After the fix, the admin config task no longer waits for
    mark_announced(), so session/ready/wait completes promptly.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _admin_config, _runtime as runtime_module
    from vibe.core.config.admin_config import ManagedConfig, ManagedConfigResult

    async def fast_fetch(_base_url: str, _api_key: str) -> ManagedConfigResult:
        return ManagedConfigResult(
            config=ManagedConfig(state="enabled", toml='theme = "nord"\n')
        )

    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: "api-key")
    monkeypatch.setattr(_admin_config, "fetch_managed_config", fast_fetch)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    services = FakeSessionBackendServices()
    process = runtime_module.HarnessProcess(experimental_harness=True)
    host = process.create_session_backend_host(cast(Any, services))
    try:
        started = await host.start(
            SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
        )
        assert started.after_response is not None
        started.after_response()
        backend = cast(Any, started.backend)
        if backend._admin_config_task is not None:
            await backend._admin_config_task

        cleared = await cast(Any, host).clear_history(
            started.backend,
            SessionHistoryClearParams(session_id=started.backend.session_id),
        )

        new_backend = cast(Any, cleared.backend)

        # Deliberately do NOT call cleared.after_response().
        # Before the fix, this would deadlock.  After the fix, the admin
        # config task completes without waiting for mark_announced().
        result = await asyncio.wait_for(
            new_backend.dispatch_extension(
                "session/ready/wait",
                SessionReadyWaitParams(session_id=new_backend.session_id).model_dump(
                    mode="json", by_alias=True
                ),
            ),
            timeout=5.0,
        )
        response = SessionReadyWaitResponse.model_validate(result.response)
        assert response.ready is True
    finally:
        await host.shutdown()
        await process.close()


@pytest.mark.asyncio
async def test_clear_ready_wait_with_slow_admin_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """session/ready/wait waits for the admin config fetch but does not deadlock.

    A slow fetch delays session/ready/wait by the fetch duration (expected:
    the first turn should run under org-overridden settings).  But it must
    complete within the fetch timeout, not hang forever.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _admin_config, _runtime as runtime_module
    from vibe.core.config.admin_config import ManagedConfig, ManagedConfigResult

    fetch_started = asyncio.Event()

    async def slow_fetch(_base_url: str, _api_key: str) -> ManagedConfigResult:
        fetch_started.set()
        await asyncio.sleep(0.5)
        return ManagedConfigResult(
            config=ManagedConfig(state="enabled", toml='theme = "nord"\n')
        )

    monkeypatch.setattr(_admin_config, "resolve_api_key", lambda _env: "api-key")
    monkeypatch.setattr(_admin_config, "fetch_managed_config", slow_fetch)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    services = FakeSessionBackendServices()
    process = runtime_module.HarnessProcess(experimental_harness=True)
    host = process.create_session_backend_host(cast(Any, services))
    try:
        started = await host.start(
            SessionStartParams(agent_config=SessionOptions(cwd=str(tmp_path)))
        )
        assert started.after_response is not None
        started.after_response()
        backend = cast(Any, started.backend)
        if backend._admin_config_task is not None:
            await backend._admin_config_task

        cleared = await cast(Any, host).clear_history(
            started.backend,
            SessionHistoryClearParams(session_id=started.backend.session_id),
        )

        new_backend = cast(Any, cleared.backend)

        # Call after_response (normal flow).
        assert cleared.after_response is not None
        cleared.after_response()

        # session/ready/wait should complete within the fetch duration,
        # not hang forever.
        result = await asyncio.wait_for(
            new_backend.dispatch_extension(
                "session/ready/wait",
                SessionReadyWaitParams(session_id=new_backend.session_id).model_dump(
                    mode="json", by_alias=True
                ),
            ),
            timeout=5.0,
        )
        response = SessionReadyWaitResponse.model_validate(result.response)
        assert response.ready is True
    finally:
        await host.shutdown()
        await process.close()
