from __future__ import annotations

from pathlib import Path
import sys
import tomllib

import pytest
import tomli_w

from tests.stubs.experiment_responses import routing_eval_response
from tests.stubs.local_sandbox import build_trusted_project
from vibe.app_server import _cached_layers as cached_layers_module
from vibe.app_server.local import LocalHarnessHost, LocalHarnessOptions
from vibe.app_server.protocol import SessionOptions
from vibe.core.agents.models import BuiltinAgentName

pytestmark = [
    pytest.mark.timeout(60),
    pytest.mark.skipif(
        sys.platform == "win32", reason="shares the POSIX sandbox test fixtures"
    ),
]

_ROUTED = "routed-model"


def _write_user_config(config_dir: Path, **values: object) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config.update(values)
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


async def _log_of_two_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> str:
    """Open two sessions in one process, the GrowthBook eval cached as at a
    warm start: the routing experiment serves ``_ROUTED``.
    """
    monkeypatch.setattr(
        cached_layers_module,
        "load_cached_eval_response",
        lambda _config, _credentials=None: routing_eval_response(_ROUTED),
    )
    project = build_trusted_project(tmp_path / "project", monkeypatch)
    options = LocalHarnessOptions(
        session_options=SessionOptions(
            cwd=str(project),
            agent=BuiltinAgentName.AUTO_APPROVE,
            trust_workspace=True,
            headless=True,
        )
    )
    host = LocalHarnessHost()
    try:
        with caplog.at_level("WARNING", logger="vibe"):
            for _ in range(2):
                session = await host.start(options)
                await session.close()
    finally:
        await host.close()
    return caplog.text


@pytest.mark.asyncio
async def test_a_pin_the_cached_experiment_serves_is_not_logged_missing(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write_user_config(
        config_dir,
        active_model=_ROUTED,
        models=[{"alias": _ROUTED, "thinking": "high"}],
    )

    log = await _log_of_two_sessions(tmp_path, monkeypatch, caplog)

    assert "is not available" not in log
    assert "are ignored" not in log


@pytest.mark.asyncio
async def test_a_model_no_layer_provides_is_logged_once_per_process(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    _write_user_config(
        config_dir,
        active_model="ghost-model",
        models=[{"alias": "ghost-settings", "thinking": "high"}],
    )

    log = await _log_of_two_sessions(tmp_path, monkeypatch, caplog)

    assert log.count("Model 'ghost-model' is not available") == 1
    assert log.count("Settings for model 'ghost-settings' are ignored") == 1
