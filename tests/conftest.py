from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Generator
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from typing import Any

import keyring
from keyring.backend import KeyringBackend
import keyring.errors
import pytest
import pytest_asyncio
import respx.mocks
import tomli_w

from tests.stubs.fake_config_orchestrator import FakeConfigOrchestrator
from tests.stubs.process_services import FakeOrgConfigServer, fake_process_services
from tests.stubs.respx_httpx2 import HTTPCore2Mocker
from vibe.app_server._process_services import ProcessServices
from vibe.app_server.connector_catalog import reset_bootstrap_failures
from vibe.core.config import (
    SessionLoggingConfig,
    VibeConfigSchema,
    VibeConfigSchemaType,
    build_default_orchestrator,
)
from vibe.core.config.harness_files import (
    HarnessFilesManager,
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.experiments.wait import reset_experiments_eval_failed
from vibe.core.model_catalog.wait import reset_model_catalog_failed
from vibe.core.utils.concurrency import run_sync
from vibe.setup.auth._oidc_credentials import clear_oidc_token_cache
from vibe.utils import keyring as keyring_utils
from vibe.utils.platform import resolve_windows_shell

respx.mocks.DEFAULT_MOCKER = HTTPCore2Mocker.name

_TESTS_ROOT = Path(__file__).parent
_HOST_PLATFORM = sys.platform
# The `VIBE_<FIELD>` names the environment config layer reads; it also reads
# `VIBE_<FIELD>__<SUBFIELD>`, case-insensitively.
_CONFIG_ENV_NAMES = frozenset(
    f"VIBE_{field.upper()}" for field in VibeConfigSchema.model_fields
)
_LOCAL_XDIST_GROUPS = {
    Path("core/test_history_properties.py"): "history_properties",
    Path("core/test_system_prompt.py"): "git_processes",
    Path("core/test_trusted_folders.py"): "git_processes",
    Path("core/test_worktree.py"): "git_processes",
}


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    local_run = not os.environ.get("BUILDKITE") and not os.environ.get("GITHUB_ACTIONS")
    relative_paths = [_relative_test_path(item) for item in items]
    # Serialize e2e CLI trees only beside the unit suite, where they would overload the machine.
    serialize_e2e = any(
        path is not None and path.parts[0] != "e2e" for path in relative_paths
    )
    for item, relative_path in zip(items, relative_paths, strict=True):
        if relative_path is None:
            continue
        if local_run and (group := _LOCAL_XDIST_GROUPS.get(relative_path)):
            item.add_marker(pytest.mark.xdist_group(name=group))
            continue
        if relative_path.parts[0] != "e2e" or not serialize_e2e:
            continue
        if relative_path.name == "test_mock_server.py":
            continue
        group = (
            "subprocess_characterization"
            if relative_path.parts[1] == "agent_loop_characterization"
            else "subprocess_cli"
        )
        item.add_marker(pytest.mark.xdist_group(name=group))


def _relative_test_path(item: pytest.Item) -> Path | None:
    try:
        return Path(item.path).relative_to(_TESTS_ROOT)
    except ValueError:
        return None


class _EmptyKeyring(KeyringBackend):
    """A keyring backend that stores nothing, used to keep tests off the real OS keyring."""

    priority = 1  # type: ignore[assignment]

    def get_password(self, service: str, username: str) -> str | None:
        return None

    def set_password(self, service: str, username: str, password: str) -> None:
        return None

    def delete_password(self, service: str, username: str) -> None:
        raise keyring.errors.PasswordDeleteError()


@pytest.fixture(autouse=True)
def _reset_windows_shell_cache() -> None:
    # resolve_windows_shell is cached for the process; tests vary sys.platform,
    # env, and shutil.which between cases, so clear it before each test.
    resolve_windows_shell.cache_clear()


@pytest.fixture(autouse=True)
def _reset_model_catalog_failed() -> None:
    # A failed catalog fetch is remembered for the process; one test's failure
    # must not stop the next from waiting.
    reset_model_catalog_failed()


@pytest.fixture(autouse=True)
def _no_provider_model_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sessions never ask a provider's model catalogue over the network; a test of
    # the catalogue sets its own fetcher factory.
    try:
        from mistralai_vibe_local_harness.vibe.completion_profile import (
            PROCESS_CATALOGUE_CACHE,
        )
        from vibe.app_server import _completion_facts
    except ImportError:
        return
    monkeypatch.setattr(_completion_facts, "catalogue_fetcher_factory", None)
    PROCESS_CATALOGUE_CACHE.clear()


@pytest.fixture(autouse=True)
def _reset_experiments_eval_failed() -> None:
    reset_experiments_eval_failed()


@pytest.fixture(autouse=True)
def _reset_connector_bootstrap_failures() -> None:
    reset_bootstrap_failures()


@pytest.fixture(autouse=True)
def _isolate_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite off the developer's git config.

    Tests create throwaway repos and real commits; without this, settings from
    ~/.gitconfig leak in: commit.gpgsign=true makes every test commit ping the
    developer's signing agent (and fail where the agent can't prompt). Repos
    that need an identity set user.name/email themselves.

    Redirecting the global/system config is not enough on every git build, so we
    also inject commit.gpgsign=false through GIT_CONFIG_COUNT, which git applies
    after all config files and thus overrides any leaked signing setting.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GCM_INTERACTIVE", "never")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "commit.gpgsign")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "false")


@pytest.fixture(autouse=True)
def _disable_os_keyring(monkeypatch: pytest.MonkeyPatch) -> Generator[None]:
    """Keep the suite off the real OS keyring.

    ``resolve_api_key`` and ``VibeConfigSchema`` API-key validation now consult the keyring, so
    without this every config construction would touch the real Keychain. We install an
    empty backend (rather than patching ``keyring.get_password``) so tests that swap in
    their own backend via ``keyring.set_keyring`` still work. Tests that exercise keyring
    behaviour opt in by patching ``keyring.get_password`` / ``set_password`` directly.
    """
    original = keyring.get_keyring()
    monkeypatch.setattr(keyring_utils, "_should_use_macos_security", lambda: False)
    keyring.set_keyring(_EmptyKeyring())
    keyring_utils.clear_api_key_keyring_cache()
    clear_oidc_token_cache()
    try:
        yield
    finally:
        keyring_utils.clear_api_key_keyring_cache()
        clear_oidc_token_cache()
        keyring.set_keyring(original)


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
        "enable_telemetry": False,
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

    # Variables exported in the developer's shell would otherwise reach every
    # test: VIBE_HOME points them all at one directory, and config settings
    # override the test config through the environment layer. Other VIBE_*
    # variables, such as CI inputs, are left alone.
    for name in list(os.environ):
        key = name.upper()
        if key == "VIBE_HOME" or key.split("__", 1)[0] in _CONFIG_ENV_NAMES:
            monkeypatch.delenv(name)
    monkeypatch.setattr("vibe.utils.vibe_home._DEFAULT_VIBE_HOME", config_dir)
    agents_dir = tmp_path / ".agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("vibe.core.paths._agents_home._DEFAULT_AGENTS_HOME", agents_dir)

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

    # Instance monkeypatches can leave restored methods bound to the singleton.
    vars(trusted_folders_manager).clear()
    trusted_folders_manager._file_path = config_dir / "trusted_folders.toml"
    trusted_folders_manager._trusted = []
    trusted_folders_manager._untrusted = []
    trusted_folders_manager._session_trusted = []


@pytest.fixture(autouse=True)
def _init_harness_files_manager():
    reset_harness_files_manager()
    init_harness_files_manager("user", "project")
    yield
    reset_harness_files_manager()


@pytest.fixture(autouse=True)
def _scratchpad_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Generator[Path]:
    scratchpad_root = tmp_path_factory.mktemp("scratchpad")
    _counter = 0

    def _fake_mkdtemp(prefix: str = "") -> str:
        nonlocal _counter
        _counter += 1
        d = scratchpad_root / f"{prefix}{_counter}"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    # Replace Scratchpad's module reference instead of mutating the process-wide
    # tempfile module, which would also affect build-backend tests.
    monkeypatch.setattr(
        "vibe.core.scratchpad.tempfile", SimpleNamespace(mkdtemp=_fake_mkdtemp)
    )

    yield scratchpad_root


@pytest.fixture(autouse=True)
def _mock_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "mock")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "mock")
    monkeypatch.setenv("OPENAI_API_KEY", "mock")
    monkeypatch.setenv("VERTEX_API_KEY", "mock")
    monkeypatch.setenv("REASONING_API_KEY", "mock")


@pytest.fixture(autouse=True)
def _no_connector_warm_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """A served app server fetches the connector catalog at start; with the
    mock key above that would be a real request.
    """
    from vibe.app_server._runtime import HarnessProcess

    monkeypatch.setattr(HarnessProcess, "start_connector_warm_up", lambda _self: None)


@pytest.fixture(autouse=True)
def _offline_production_services(monkeypatch: pytest.MonkeyPatch) -> None:
    """Services an entry point builds itself (``LocalHarnessHost``, stdio) fetch
    from an offline endpoint: with the mock key above, a real request.
    """
    offline = FakeOrgConfigServer()
    monkeypatch.setattr(
        ProcessServices,
        "create",
        classmethod(lambda _cls: fake_process_services(offline)),
    )


@pytest.fixture
def org_server() -> FakeOrgConfigServer:
    """The managed-config endpoint ``process_services`` fetch from: offline unless
    the test serves an answer.
    """
    return FakeOrgConfigServer()


@pytest_asyncio.fixture
async def process_services(
    org_server: FakeOrgConfigServer,
) -> AsyncIterator[ProcessServices]:
    """This test's process services, as an entry point would build them."""
    services = fake_process_services(org_server)
    yield services
    await services.close()


@pytest.fixture(autouse=True)
def _mock_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the platform to Linux with /bin/sh on POSIX hosts.

    This keeps platform-specific system prompt generation consistent on the
    Linux CI runners and macOS dev machines. On Windows the real platform
    stays, so the suite exercises the Windows branches there (DSK-1495).
    """
    if _HOST_PLATFORM == "win32":
        return
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("SHELL", "/bin/sh")


@pytest.fixture(autouse=True)
def _empty_login_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Keep the developer's shell startup files out of commands the tests run.

    The Unified Harness picks zsh when no shell is configured and reads its
    login profile once per session; a slow profile made shell tests time out.
    ``ZDOTDIR`` names an empty directory, so zsh reads none of the user's
    files, and the variables naming a startup file for sh and bash are unset.
    ``HOME`` is left alone: tests rely on it for git and tool caches.
    """
    monkeypatch.setenv("ZDOTDIR", str(tmp_path_factory.mktemp("zdotdir")))
    for name in ("BASH_ENV", "ENV"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def host_platform(_mock_platform: None, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(sys, "platform", _HOST_PLATFORM)
    return _HOST_PLATFORM


@pytest.fixture(autouse=True)
def _isolate_model_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep availability checks off the network; reset their state per test.

    A test that needs a verdict records one with ``MODEL_AVAILABILITY.remember``.
    """
    from vibe.core.llm import model_probe

    class _NoVerdictSource:
        async def check(self, **_: Any) -> dict[str, bool]:
            return {}

    async def _no_verdict(**_: Any) -> None:
        return None

    monkeypatch.setattr(model_probe, "_probe", _no_verdict)
    monkeypatch.setattr(model_probe.MODEL_AVAILABILITY, "_source", _NoVerdictSource())
    model_probe.MODEL_AVAILABILITY.reset()


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
        merged = self.build_client_event_metadata() | properties
        event: dict[str, Any] = {"event_name": event_name, "properties": merged}
        if correlation_id is not None:
            event["correlation_id"] = correlation_id
        events.append(event)

    monkeypatch.setattr(
        "vibe.core.telemetry.send.TelemetryClient.send_telemetry_event",
        record_telemetry,
    )
    return events


@pytest.fixture
def mock_prompts_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path]:
    project = tmp_path / "project" / ".vibe" / "prompts"
    user = tmp_path / "home" / ".vibe" / "prompts"
    project.mkdir(parents=True)
    user.mkdir(parents=True)

    class _MockManager(HarnessFilesManager):
        @property
        def project_prompts_dirs(self) -> list[Path]:
            return [project]

        @property
        def user_prompts_dirs(self) -> list[Path]:
            return [user]

    monkeypatch.setattr(
        "vibe.core.prompts.get_harness_files_manager",
        lambda: _MockManager(sources=("user",)),
    )
    return project, user


@pytest.fixture
def vibe_config() -> VibeConfigSchema:
    return build_test_vibe_config()


@pytest.fixture(params=[VibeConfigSchema], ids=["vibe_config_schema"])
def config_cls(request: pytest.FixtureRequest) -> VibeConfigSchemaType:
    return request.param


@pytest.fixture
def make_config(config_cls: VibeConfigSchemaType) -> Callable[..., VibeConfigSchema]:
    def _make(**kwargs: Any) -> VibeConfigSchema:
        return build_test_vibe_config(config_cls=config_cls, **kwargs)

    return _make


@pytest.fixture
def make_orchestrator() -> Callable[
    [], Awaitable[ConfigOrchestrator[VibeConfigSchema]]
]:
    """Build the default config orchestrator lazily.

    The factory is async because the orchestrator builds its layer stack lazily;
    call it after seeding the on-disk config.
    """

    async def _make() -> ConfigOrchestrator[VibeConfigSchema]:
        return await build_default_orchestrator()

    return _make


def _prepare_test_config_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    session_logging = kwargs.pop("session_logging", None)
    kwargs["session_logging"] = (
        SessionLoggingConfig(enabled=False)
        if session_logging is None
        else session_logging
    )
    enable_update_checks = kwargs.pop("enable_update_checks", None)
    kwargs["enable_update_checks"] = (
        False if enable_update_checks is None else enable_update_checks
    )
    if models := kwargs.get("models"):
        if isinstance(models, dict):
            kwargs.setdefault("active_model", next(iter(models)))
        else:
            kwargs.setdefault("active_model", models[0].alias)
    # Connectors trigger a real HTTP discovery on agent construction; off by
    # default so tests don't pay for it. Connector tests pass enable_connectors=True.
    kwargs.setdefault("enable_connectors", False)
    # Telemetry gates the remote experiment fetch (experiments.mistral.services);
    # off by default so a leaked MISTRAL_API_KEY never triggers an unmocked call.
    kwargs.setdefault("enable_telemetry", False)
    # Use the lightweight test system prompt unless a test asks for a real one.
    kwargs.setdefault("system_prompt_id", "tests")
    # Keep the test prompt minimal: skip project-context discovery unless a
    # test opts in.
    kwargs.setdefault("include_project_context", False)
    return kwargs


async def wait_until(
    pilot: Any, predicate: Callable[[], bool], timeout: float = 2.0
) -> bool:
    """Poll the Textual app until ``predicate`` holds, returning whether it did.

    Returns ``True`` as soon as ``predicate()`` is truthy, or ``False`` if the
    timeout elapses first. It deliberately does not raise: callers assert the
    result (``assert await wait_until(...)`` to require the state, or
    ``assert not await wait_until(...)`` to require a state stays absent for the
    window).

    The short ``pilot.pause`` delay both yields to the message pump and advances
    wall-clock time so timer/debounce-driven UI state settles. This is *not* a
    substitute for synchronising on real state: wait on *authoritative* signals
    (e.g. server-confirmed queue length via ``app.app_server``) rather than the
    client's optimistic projections (``len(app._queue)`` is set before the enqueue
    RPC lands), which is a common source of ordering flakes.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.05)
    return predicate()


def build_test_vibe_config(
    config_cls: VibeConfigSchemaType = VibeConfigSchema, **kwargs
) -> VibeConfigSchema:
    return config_cls(**_prepare_test_config_kwargs(kwargs))


def build_test_vibe_config_schema(**kwargs) -> VibeConfigSchema:
    return VibeConfigSchema(**_prepare_test_config_kwargs(kwargs))


type ConfigBuilder = Callable[..., VibeConfigSchema]


@pytest.fixture(params=[build_test_vibe_config_schema], ids=["vibe_config_schema"])
def build_config(request: pytest.FixtureRequest) -> ConfigBuilder:
    return request.param


type ConfigLoader = Callable[[], VibeConfigSchema]


def _load_vibe_config_schema() -> VibeConfigSchema:
    return run_sync(build_default_orchestrator()).config


@pytest.fixture(params=[_load_vibe_config_schema], ids=["vibe_config_schema"])
def load_config(request: pytest.FixtureRequest) -> ConfigLoader:
    """Loader that reads the persisted config through the orchestrator layer stack."""
    return request.param


type OrchestratorLoader[C: VibeConfigSchema] = Callable[[C], ConfigOrchestrator[C]]


async def _load_orchestrator(
    config: VibeConfigSchema,
) -> ConfigOrchestrator[VibeConfigSchema]:
    return FakeConfigOrchestrator(config)


@pytest.fixture
def load_orchestrator() -> OrchestratorLoader[VibeConfigSchema]:
    """Wraps a config into the default orchestrator by injecting its set fields
    into the highest-priority OverridesLayer, exposed via ConfigOrchestrator.
    """

    def load(config: VibeConfigSchema) -> ConfigOrchestrator[VibeConfigSchema]:
        return run_sync(_load_orchestrator(config))

    return load
