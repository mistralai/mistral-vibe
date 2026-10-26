"""Unified Harness adapter tests for refused config mutations: a reload or a
write the configuration refuses fails loudly, with the reason, and the session
keeps the configuration it has.

Split from ``test_unified_harness_backend_adapter.py``, which keeps the adapter's
core behaviour and the helpers these tests share.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest

from tests.app_server.test_unified_harness_backend_adapter import (
    _admin_result,
    _RecordingSession,
    _stub_unified_context,
)
from tests.conftest import build_test_vibe_config
from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from vibe.app_server._session_backend_port import SessionBackendError
from vibe.app_server.protocol import AppServerResponseError, ProtocolErrorCode
from vibe.core.config import VibeConfigSchema
from vibe.core.config.admin_config import AdminConfigOutcome


@pytest.mark.asyncio
async def test_unified_config_reload_reports_a_refused_config_as_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """*Prepare*: A session whose reload builds a config the running agent
    refuses.
    *Do*: Reload the config.
    *Assert*: Invalid params of kind configuration with the refusal; the
    session keeps its derivation, nothing is applied.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _unified_harness_backend_adapter as adapter_module
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )
    from vibe.app_server.protocol import ConfigReloadParams
    from vibe.core.config.builder import InvalidConfigError

    refusal = "The 'lean' agent runs tools.bash with max_timeout_seconds 1200"

    class RefusingOrchestrator(FakeConfigOrchestrator[VibeConfigSchema]):
        async def reload(self, **_kwargs: object) -> None:
            raise InvalidConfigError(refusal)

    async def no_admin_fetch(_orchestrator: object) -> object:
        return _admin_result(AdminConfigOutcome.DISABLED)

    monkeypatch.setattr(adapter_module, "fetch_admin_toml", no_admin_fetch)
    orchestrator = RefusingOrchestrator(build_test_vibe_config())
    context, derivation = _stub_unified_context(tmp_path, orchestrator)
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(cast(Any, session), context, derivation)

    with pytest.raises(SessionBackendError) as caught:
        await adapter.reload_config(
            ConfigReloadParams(session_id=_RecordingSession.session_id)
        )

    assert caught.value.code == ProtocolErrorCode.INVALID_PARAMS
    assert str(caught.value) == f"Cannot load the Vibe config. {refusal}"
    assert caught.value.data == {"kind": "configuration"}
    assert session.applied == []


@pytest.mark.asyncio
async def test_a_config_write_the_running_agent_refuses_returns_the_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """*Prepare*: A session whose config write builds a config the running
    agent refuses.
    *Do*: Write the config.
    *Assert*: A rejected response with the refusal as its reason, logged at
    warning; nothing is applied.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )
    from vibe.app_server.protocol import ConfigWriteOpWire, ConfigWriteParams
    from vibe.core.config.builder import InvalidConfigError

    refusal = "The 'lean' agent runs tools.bash with max_timeout_seconds 1200"

    class RefusingOrchestrator(FakeConfigOrchestrator[VibeConfigSchema]):
        async def apply_patch(self, *_args: object, **_kwargs: object) -> list:
            raise InvalidConfigError(refusal)

    orchestrator = RefusingOrchestrator(build_test_vibe_config())
    context, derivation = _stub_unified_context(tmp_path, orchestrator)
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(cast(Any, session), context, derivation)

    with caplog.at_level(logging.WARNING):
        result = await adapter.write_config(
            ConfigWriteParams(
                session_id=_RecordingSession.session_id,
                ops=[
                    ConfigWriteOpWire(
                        op="set", path="/tools/bash/max_timeout_seconds", value=1200
                    )
                ],
            )
        )

    assert result.response.rejected is True
    assert result.response.reason == refusal
    assert any(
        record.levelno == logging.WARNING and refusal in record.getMessage()
        for record in caplog.records
    )
    assert session.applied == []


@pytest.mark.parametrize(
    ("reason", "message"),
    [
        ("lean clashes", "Invalid configuration edit: lean clashes"),
        (None, "Invalid configuration edit"),
    ],
)
def test_a_rejected_config_write_raises_with_its_reason(
    reason: str | None, message: str
) -> None:
    """*Prepare*: A rejected config write, with and without a reason.
    *Do*: Turn it into the Python client's error.
    *Assert*: Invalid params whose message carries the reason when there is one.
    """
    from vibe.app_server._runtime_resources import _raise_for_write
    from vibe.app_server.protocol import ConfigWriteResponse

    with pytest.raises(AppServerResponseError) as caught:
        _raise_for_write(
            ConfigWriteResponse.model_construct(rejected=True, reason=reason)
        )

    assert caught.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert caught.value.error.message == message


@pytest.mark.asyncio
async def test_a_config_reload_the_mcp_catalog_cannot_build_is_a_configuration_error() -> (
    None
):
    """*Prepare*: A server whose MCP catalog reloads the config first, on a
    config the running agent refuses.
    *Do*: Dispatch ``config/reload``.
    *Assert*: The configuration error with the refusal, before the session's
    own reload runs.
    """
    from vibe.app_server.server import AppServer
    from vibe.app_server.transport import memory_transport_pair
    from vibe.core.config.builder import InvalidConfigError

    refusal = "The 'lean' agent runs tools.bash with max_timeout_seconds 1200"

    class RefusingCatalog:
        async def prepare_config_reload(self, _root: object) -> None:
            raise InvalidConfigError(refusal)

    root = Mock()
    _, transport = memory_transport_pair()
    server = AppServer(
        transport,
        session_backend_host_factory=lambda _: cast(Any, object()),
        mcp_catalog_service=cast(Any, RefusingCatalog()),
    )

    with pytest.raises(SessionBackendError) as caught:
        await server._dispatch_backend_config(root, "config/reload", {"sessionId": "s"})

    assert caught.value.code == ProtocolErrorCode.INVALID_PARAMS
    assert str(caught.value) == f"Cannot load the Vibe config. {refusal}"
    assert caught.value.data == {"kind": "configuration"}
    root.reload_config.assert_not_called()


@pytest.mark.asyncio
async def test_a_config_reload_of_a_config_toml_the_schema_refuses_is_a_configuration_error(
    tmp_path: Path, config_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Prepare*: A live session, then a direct edit of `config.toml` that sets
    `tools.bash.default_timeout` without `max_timeout_seconds`.
    *Do*: Reload the config.
    *Assert*: Invalid params of kind configuration naming both settings, as for
    any other config the user must fix; the session keeps its configuration.
    """
    pytest.importorskip("mistralai_vibe_local_harness.vibe")
    from vibe.app_server import _unified_harness_backend_adapter as adapter_module
    from vibe.app_server._runtime import HarnessProcess
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
        UnifiedSessionSettings,
    )
    from vibe.app_server.protocol import ConfigReloadParams, SessionOptions

    async def no_admin_fetch(_orchestrator: object) -> object:
        return _admin_result(AdminConfigOutcome.DISABLED)

    monkeypatch.setattr(adapter_module, "fetch_admin_toml", no_admin_fetch)
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    process = HarnessProcess()
    context = await process.build_unified_session_context(
        SessionOptions(cwd=str(tmp_path))
    )
    session = _RecordingSession()
    adapter = UnifiedHarnessBackendAdapter(
        cast(Any, session), context, context.derive(UnifiedSessionSettings())
    )
    config_file = config_dir / "config.toml"
    config_file.write_text(
        config_file.read_text(encoding="utf-8")
        + "\n[tools.bash]\ndefault_timeout = 600\n",
        encoding="utf-8",
    )

    try:
        with pytest.raises(SessionBackendError) as caught:
            await adapter.reload_config(
                ConfigReloadParams(session_id=_RecordingSession.session_id)
            )
    finally:
        await process.close()

    assert caught.value.code == ProtocolErrorCode.INVALID_PARAMS
    assert caught.value.data == {"kind": "configuration"}
    assert "tools.bash: default_timeout is set without max_timeout_seconds" in str(
        caught.value
    )
    assert session.applied == []
