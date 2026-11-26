from __future__ import annotations

import pytest

from tests.conftest import build_test_vibe_config
import vibe.acp.entrypoint as entrypoint


def _forbid(*_args: object, **_kwargs: object) -> None:
    raise AssertionError("ACP startup ran")


def _record_exec(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    calls: list[dict[str, object]] = []

    def _record(passthrough: list[str], **kwargs: object) -> None:
        calls.append({"passthrough": passthrough, **kwargs})

    monkeypatch.setattr(entrypoint, "exec_rust_cli", _record)
    monkeypatch.setattr(entrypoint, "bootstrap_vibe_home", _forbid)
    monkeypatch.setattr("sys.argv", ["vibe-acp", "--setup"])
    return calls


@pytest.mark.parametrize(
    ("api_base", "telemetry", "enabled"),
    [
        ("https://api.mistral.ai/v1", True, True),
        ("https://api.customer.mistral.ai/v1", True, True),
        ("https://api.asml.example/v1", True, False),
        ("https://api.mistral.ai/v1", False, False),
    ],
)
def test_crash_reports_follow_telemetry_and_stay_off_self_hosted(
    api_base: str, telemetry: bool, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from vibe.core.config import ProviderConfig
    from vibe.core.types import Backend
    import vibe.observability.sentry as sentry

    calls: list[bool] = []
    monkeypatch.setattr(
        sentry, "init_sentry", lambda **kwargs: calls.append(kwargs["enabled"])
    )
    # A DSN in the environment does not turn reports on for a self-hosted host.
    monkeypatch.setenv("SENTRY_DSN", "https://key@sentry.example/1")
    config = build_test_vibe_config(
        enable_telemetry=telemetry,
        providers=[
            ProviderConfig(
                name="mistral",
                api_base=api_base,
                api_key_env_var="MISTRAL_API_KEY",
                backend=Backend.MISTRAL,
            )
        ],
    )

    entrypoint._start_crash_reporting(config)

    assert calls == [enabled]


def test_setup_runs_the_rust_wizard_before_acp_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record_exec(monkeypatch)

    entrypoint.main()

    assert calls == [
        {
            "passthrough": ["--setup"],
            "extra_env": {"VIBE_SETUP_ENTRYPOINT": "acp"},
            "app_server_cmd": None,
        }
    ]


def test_frozen_setup_serves_the_app_server_from_its_own_executable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _record_exec(monkeypatch)
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys.executable", "/bundle/vibe-acp")

    entrypoint.main()

    (call,) = calls
    assert call["app_server_cmd"] == ["/bundle/vibe-acp", "--setup-app-server"]


def test_setup_app_server_flag_serves_the_app_server_with_the_rest_of_argv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    served_with: list[list[str]] = []
    monkeypatch.setattr("vibe.app_server.stdio.main", served_with.append)
    monkeypatch.setattr(entrypoint, "bootstrap_vibe_home", _forbid)
    monkeypatch.setattr("sys.argv", ["vibe-acp", "--setup-app-server", "--multiplex"])

    entrypoint.main()

    assert served_with == [["--multiplex"]]
