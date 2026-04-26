from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import pytest
import tomli_w

from tests.cli.plan_offer.adapters.fake_whoami_gateway import FakeWhoAmIGateway
from tests.stubs.fake_backend import FakeBackend
from tests.stubs.fake_voice_manager import FakeVoiceManager
from tests.update_notifier.adapters.fake_update_cache_repository import (
    FakeUpdateCacheRepository,
)
from tests.update_notifier.adapters.fake_update_gateway import FakeUpdateGateway
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIPlanType, WhoAmIResponse
from vibe.cli.textual_ui.app import CORE_VERSION, StartupOptions, VibeApp
from vibe.core.agent_loop import AgentLoop
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config import (
    DEFAULT_MODELS,
    ModelConfig,
    SessionLoggingConfig,
    VibeConfig,
)
from vibe.core.config.harness_files import (
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.llm.types import BackendLike


def get_base_config() -> dict[str, Any]:
    return {
        "active_model": "devstral-latest",
        "providers": [
            {
                "name": "mistral",
                "api_base": "https://api.mistral.ai/v1",
                "api_key_env_var": "MISTRAL_API_KEY",
                "browser_auth_base_url": "https://console.mistral.ai",
                "browser_auth_api_base_url": "https://console.mistral.ai/api",
                "backend": "mistral",
            }
        ],
        "models": [
            {
                "name": "mistral-vibe-cli-latest",
                "provider": "mistral",
                "alias": "devstral-latest",
            }
        ],
        "enable_auto_update": False,
    }


@pytest.fixture(autouse=True)
def tmp_working_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    tmp_working_directory = tmp_path_factory.mktemp("test_cwd")
    monkeypatch.chdir(tmp_working_directory)
    return tmp_working_directory


@pytest.fixture(autouse=True)
def config_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    tmp_path = tmp_path_factory.mktemp("vibe")
    config_dir = tmp_path / ".vibe"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_file = config_dir / "config.toml"
    config_file.write_text(tomli_w.dumps(get_base_config()), encoding="utf-8")

    monkeypatch.setattr("vibe.core.paths._vibe_home._DEFAULT_VIBE_HOME", config_dir)

    # Re-evaluate PLAN agent overrides so the allowlist uses the monkeypatched path
    from vibe.core.agents.models import PLAN, _plan_overrides

    object.__setattr__(PLAN, "overrides", _plan_overrides())

    return config_dir


@pytest.fixture(autouse=True)
def _reset_trusted_folders_manager(config_dir: Path) -> None:
    """Prevent the singleton from writing to the real ~/.vibe/trusted_folders.toml.

    The module-level ``trusted_folders_manager`` captures its file path at import
    time (before any monkeypatch), so it would otherwise target the real home
    directory.  Redirect it to the temp config dir used by the ``config_dir``
    fixture.
    """
    from vibe.core.trusted_folders import trusted_folders_manager

    trusted_folders_manager._file_path = config_dir / "trusted_folders.toml"
    trusted_folders_manager._trusted = []
    trusted_folders_manager._untrusted = []


@pytest.fixture(autouse=True)
def _init_harness_files_manager():
    reset_harness_files_manager()
    init_harness_files_manager("user", "project")
    yield
    reset_harness_files_manager()


@pytest.fixture(autouse=True)
def _mock_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "mock")


@pytest.fixture(autouse=True)
def _mock_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock platform to be Linux with /bin/sh shell for consistent test behavior.

    This ensures that platform-specific system prompt generation is consistent
    across all tests regardless of the actual platform running the tests.
    """
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("SHELL", "/bin/sh")


@pytest.fixture(autouse=True)
def _mock_update_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("vibe.cli.update_notifier.update.UPDATE_COMMANDS", ["true"])


@pytest.fixture(autouse=True)
def _disable_feedback_bar(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.feedback_bar.FEEDBACK_PROBABILITY", 0
    )


@pytest.fixture(autouse=True)
def telemetry_events(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    def record_telemetry(
        self: Any,
        event_name: str,
        properties: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> None:
        event: dict[str, Any] = {"event_name": event_name, "properties": properties}
        if correlation_id is not None:
            event["correlation_id"] = correlation_id
        events.append(event)

    monkeypatch.setattr(
        "vibe.core.telemetry.send.TelemetryClient.send_telemetry_event",
        record_telemetry,
    )
    return events


@pytest.fixture
def vibe_app() -> VibeApp:
    return build_test_vibe_app()


@pytest.fixture
def agent_loop() -> AgentLoop:
    return build_test_agent_loop()


@pytest.fixture
def vibe_config() -> VibeConfig:
    return build_test_vibe_config()


def make_test_models(auto_compact_threshold: int) -> list[ModelConfig]:
    return [
        m.model_copy(update={"auto_compact_threshold": auto_compact_threshold})
        for m in DEFAULT_MODELS
    ]


def build_test_vibe_config(**kwargs) -> VibeConfig:
    session_logging = kwargs.pop("session_logging", None)
    resolved_session_logging = (
        SessionLoggingConfig(enabled=False)
        if session_logging is None
        else session_logging
    )
    enable_update_checks = kwargs.pop("enable_update_checks", None)
    resolved_enable_update_checks = (
        False if enable_update_checks is None else enable_update_checks
    )
    if kwargs.get("models"):
        kwargs.setdefault("active_model", kwargs["models"][0].alias)
    return VibeConfig(
        session_logging=resolved_session_logging,
        enable_update_checks=resolved_enable_update_checks,
        **kwargs,
    )


def build_test_agent_loop(
    *,
    config: VibeConfig | None = None,
    agent_name: str = BuiltinAgentName.DEFAULT,
    backend: BackendLike | None = None,
    enable_streaming: bool = False,
    **kwargs,
) -> AgentLoop:

    resolved_config = config or build_test_vibe_config()

    return AgentLoop(
        config=resolved_config,
        agent_name=agent_name,
        backend=backend or FakeBackend(),
        enable_streaming=enable_streaming,
        **kwargs,
    )


def build_test_vibe_app(
    *, config: VibeConfig | None = None, agent_loop: AgentLoop | None = None, **kwargs
) -> VibeApp:
    app_config = config or build_test_vibe_config()

    resolved_agent_loop = agent_loop or build_test_agent_loop(config=app_config)

    update_notifier = kwargs.pop("update_notifier", None)
    resolved_update_notifier = (
        FakeUpdateGateway() if update_notifier is None else update_notifier
    )
    update_cache_repository = kwargs.pop("update_cache_repository", None)
    resolved_update_cache_repository = (
        FakeUpdateCacheRepository()
        if update_cache_repository is None
        else update_cache_repository
    )
    plan_offer_gateway = kwargs.pop("plan_offer_gateway", None)
    resolved_plan_offer_gateway = (
        FakeWhoAmIGateway(
            WhoAmIResponse(
                plan_type=WhoAmIPlanType.CHAT,
                plan_name="INDIVIDUAL",
                prompt_switching_to_pro_plan=False,
            )
        )
        if plan_offer_gateway is None
        else plan_offer_gateway
    )
    current_version = kwargs.pop("current_version", None)
    resolved_current_version = (
        CORE_VERSION if current_version is None else current_version
    )
    voice_manager = kwargs.pop("voice_manager", FakeVoiceManager())

    return VibeApp(
        agent_loop=resolved_agent_loop,
        startup=StartupOptions(initial_prompt=kwargs.pop("initial_prompt", None)),
        current_version=resolved_current_version,
        update_notifier=resolved_update_notifier,
        update_cache_repository=resolved_update_cache_repository,
        plan_offer_gateway=resolved_plan_offer_gateway,
        voice_manager=voice_manager,
        **kwargs,
    )
