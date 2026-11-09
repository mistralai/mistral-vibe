from __future__ import annotations

import asyncio
from pathlib import Path
import tomllib
from typing import Any

import pytest
import tomli_w

from tests.stubs.process_services import FakeOrgConfigServer
from vibe.app_server._org_config_service import OrgConfigService
from vibe.app_server._process_services import ProcessServices
import vibe.app_server._runtime as runtime_module
from vibe.app_server._session_config_cache import (
    BuiltSessionConfig,
    SessionConfigCache,
    SessionConfigKey,
)
from vibe.app_server.protocol import SessionOptions
from vibe.core.config import build_default_orchestrator
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.trusted_folders import trusted_folders_manager
from vibe.utils.api_keys import session_credentials


class _CountedBuilds:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.count = 0
        build = runtime_module.build_default_orchestrator

        async def counted(*args: Any, **kwargs: Any) -> Any:
            self.count += 1
            return await build(*args, **kwargs)

        monkeypatch.setattr(runtime_module, "build_default_orchestrator", counted)


@pytest.fixture
def builds(monkeypatch: pytest.MonkeyPatch) -> _CountedBuilds:
    return _CountedBuilds(monkeypatch)


@pytest.fixture
def process(process_services: ProcessServices) -> runtime_module.HarnessProcess:
    return runtime_module.HarnessProcess(services=process_services)


def _write_user_config(config_dir: Path, **settings: Any) -> None:
    config_file = config_dir / "config.toml"
    config = tomllib.loads(config_file.read_text(encoding="utf-8"))
    config.update(settings)
    config_file.write_text(tomli_w.dumps(config), encoding="utf-8")


def _write_project_config(root: Path) -> Path:
    config_file = root / ".vibe" / "config.toml"
    config_file.parent.mkdir(parents=True, exist_ok=True)
    config_file.write_text('theme = "project"\n', encoding="utf-8")
    return config_file


@pytest.mark.asyncio
async def test_reads_with_unchanged_inputs_share_one_build(
    tmp_path: Path, process: runtime_module.HarnessProcess, builds: _CountedBuilds
) -> None:
    options = SessionOptions(cwd=str(tmp_path))

    first = await process.build_unified_read_context(options)
    second = await process.build_unified_read_context(options)

    assert builds.count == 1
    assert first.config_orchestrator is not second.config_orchestrator
    assert first.config == second.config


@pytest.mark.asyncio
async def test_a_write_to_one_read_does_not_reach_the_next(
    tmp_path: Path, process: runtime_module.HarnessProcess
) -> None:
    options = SessionOptions(cwd=str(tmp_path))
    first = await process.build_unified_read_context(options)
    await first.config_orchestrator.set_field(
        "/theme", "changed", target_layer="overrides"
    )

    second = await process.build_unified_read_context(options)

    assert first.config.theme == "changed"
    assert second.config.theme != "changed"


@pytest.mark.asyncio
async def test_a_read_sees_an_edit_to_the_user_config(
    tmp_path: Path, config_dir: Path, process: runtime_module.HarnessProcess
) -> None:
    options = SessionOptions(cwd=str(tmp_path))
    _write_user_config(
        config_dir, session_logging={"save_dir": str(tmp_path / "before")}
    )
    before = await process.build_unified_read_context(options)

    _write_user_config(
        config_dir, session_logging={"save_dir": str(tmp_path / "after-the-edit")}
    )
    after = await process.build_unified_read_context(options)

    assert before.storage_root == str(tmp_path / "before")
    assert after.storage_root == str(tmp_path / "after-the-edit")


@pytest.mark.asyncio
async def test_a_project_config_created_above_the_cwd_rebuilds(
    tmp_path: Path, process: runtime_module.HarnessProcess, builds: _CountedBuilds
) -> None:
    cwd = tmp_path / "repo" / "src"
    cwd.mkdir(parents=True)
    trusted_folders_manager.add_trusted(tmp_path / "repo")
    options = SessionOptions(cwd=str(cwd))
    await process.build_unified_read_context(options)

    _write_project_config(tmp_path / "repo")
    context = await process.build_unified_read_context(options)

    assert builds.count == 2
    assert context.config.theme == "project"


@pytest.mark.asyncio
async def test_trusting_the_project_config_rebuilds(
    tmp_path: Path, process: runtime_module.HarnessProcess
) -> None:
    _write_project_config(tmp_path)
    options = SessionOptions(cwd=str(tmp_path))
    untrusted = await process.build_unified_read_context(options)

    trusted_folders_manager.add_trusted(tmp_path)
    trusted = await process.build_unified_read_context(options)

    assert untrusted.config.theme != "project"
    assert trusted.config.theme == "project"


@pytest.mark.asyncio
async def test_an_environment_change_rebuilds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process: runtime_module.HarnessProcess,
) -> None:
    options = SessionOptions(cwd=str(tmp_path))
    await process.build_unified_read_context(options)

    monkeypatch.setenv("VIBE_THEME", "from-the-environment")
    context = await process.build_unified_read_context(options)

    assert context.config.theme == "from-the-environment"


@pytest.mark.asyncio
async def test_a_new_org_config_the_process_holds_rebuilds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    org_server: FakeOrgConfigServer,
    process_services: ProcessServices,
    process: runtime_module.HarnessProcess,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "a-key")
    options = SessionOptions(cwd=str(tmp_path))
    before = await process.build_unified_read_context(options)

    org_server.serve('default_agent = "ask"\n')
    await process_services.org_config.refresh(
        OrgConfigService.key_for_config(before.config)
    )
    after = await process.build_unified_read_context(options)

    assert before.config.default_agent != "ask"
    assert after.config.default_agent == "ask"


@pytest.mark.asyncio
async def test_another_credential_rebuilds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    process: runtime_module.HarnessProcess,
    builds: _CountedBuilds,
) -> None:
    monkeypatch.setenv("MISTRAL_API_KEY", "first-key")
    options = SessionOptions(cwd=str(tmp_path))
    await process.build_unified_read_context(options)

    with session_credentials({"MISTRAL_API_KEY": "second-key"}):
        await process.build_unified_read_context(options)

    assert builds.count == 2


@pytest.mark.asyncio
async def test_other_cwds_and_overrides_build_their_own(
    tmp_path: Path, process: runtime_module.HarnessProcess, builds: _CountedBuilds
) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()

    await process.build_unified_read_context(SessionOptions(cwd=str(tmp_path / "a")))
    await process.build_unified_read_context(SessionOptions(cwd=str(tmp_path / "b")))
    await process.build_unified_read_context(
        SessionOptions(cwd=str(tmp_path / "a"), disabled_tools=["bash"])
    )

    assert builds.count == 3


@pytest.mark.asyncio
async def test_reads_arriving_together_share_one_build(
    tmp_path: Path, process: runtime_module.HarnessProcess, builds: _CountedBuilds
) -> None:
    options = SessionOptions(cwd=str(tmp_path))

    contexts = await asyncio.gather(
        *(process.build_unified_read_context(options) for _ in range(5))
    )

    assert builds.count == 1
    assert len({id(context.config_orchestrator) for context in contexts}) == 5


class _CountedCacheBuilds:
    def __init__(self, harness_files: HarnessFilesManager) -> None:
        self.count = 0
        self._harness_files = harness_files
        self.pre_org_api_key_env_var: str | None = None
        self.release: asyncio.Event | None = None
        self.fail = False

    async def __call__(self) -> BuiltSessionConfig:
        self.count += 1
        if self.release is not None:
            await self.release.wait()
        if self.fail:
            raise RuntimeError("build failed")
        orchestrator = await build_default_orchestrator(
            harness_files=self._harness_files
        )
        pre_org_config = orchestrator.config
        if self.pre_org_api_key_env_var is not None:
            pre_org_config = pre_org_config.model_copy(
                update={
                    "providers": [
                        provider.model_copy(
                            update={"api_key_env_var": self.pre_org_api_key_env_var}
                        )
                        for provider in pre_org_config.providers
                    ]
                }
            )
        return BuiltSessionConfig(
            orchestrator,
            pre_org_config=pre_org_config,
            org_config_key=OrgConfigService.key_for_config(pre_org_config),
            org_config_tried=None,
            org_config_failure=None,
        )


@pytest.mark.asyncio
async def test_a_build_older_than_the_backstop_is_rebuilt(tmp_path: Path) -> None:
    now = 0.0
    cache = SessionConfigCache(clock=lambda: now)
    harness_files = HarnessFilesManager(sources=("user",)).for_session(tmp_path)
    key = SessionConfigKey.of(tmp_path, {}, org_config=True)
    build = _CountedCacheBuilds(harness_files)

    await cache.get(key, harness_files, None, build)
    now = 60.0
    await cache.get(key, harness_files, None, build)
    now = 3600.0
    await cache.get(key, harness_files, None, build)

    assert build.count == 2


@pytest.mark.asyncio
async def test_another_credential_for_the_provider_before_the_org_config_rebuilds(
    tmp_path: Path,
) -> None:
    # The org config moved the Mistral provider to another key: the org config
    # itself is held under the key from before it applied.
    cache = SessionConfigCache()
    harness_files = HarnessFilesManager(sources=("user",)).for_session(tmp_path)
    key = SessionConfigKey.of(tmp_path, {}, org_config=True)
    build = _CountedCacheBuilds(harness_files)
    build.pre_org_api_key_env_var = "PRE_ORG_MISTRAL_API_KEY"

    with session_credentials({"PRE_ORG_MISTRAL_API_KEY": "first-account"}):
        await cache.get(key, harness_files, None, build)
    with session_credentials({"PRE_ORG_MISTRAL_API_KEY": "second-account"}):
        await cache.get(key, harness_files, None, build)

    assert build.count == 2


def _gated_cache(
    tmp_path: Path,
) -> tuple[
    SessionConfigCache, HarnessFilesManager, SessionConfigKey, _CountedCacheBuilds
]:
    harness_files = HarnessFilesManager(sources=("user",)).for_session(tmp_path)
    build = _CountedCacheBuilds(harness_files)
    build.release = asyncio.Event()
    return (
        SessionConfigCache(),
        harness_files,
        SessionConfigKey.of(tmp_path, {}, org_config=True),
        build,
    )


@pytest.mark.asyncio
async def test_a_failed_build_fails_every_waiter_and_is_not_kept(
    tmp_path: Path,
) -> None:
    cache, harness_files, key, build = _gated_cache(tmp_path)
    build.fail = True
    waiting = [
        asyncio.create_task(cache.get(key, harness_files, None, build))
        for _ in range(2)
    ]
    await asyncio.sleep(0)
    assert build.release is not None
    build.release.set()

    results = await asyncio.gather(*waiting, return_exceptions=True)
    build.fail = False
    await cache.get(key, harness_files, None, build)

    assert [type(result) for result in results] == [RuntimeError, RuntimeError]
    assert build.count == 2


@pytest.mark.asyncio
async def test_a_reader_that_stops_waiting_does_not_stop_the_others(
    tmp_path: Path,
) -> None:
    cache, harness_files, key, build = _gated_cache(tmp_path)
    leaving = asyncio.create_task(cache.get(key, harness_files, None, build))
    staying = asyncio.create_task(cache.get(key, harness_files, None, build))
    await asyncio.sleep(0)

    leaving.cancel()
    assert build.release is not None
    build.release.set()
    built = await staying

    assert leaving.cancelled()
    assert built.config_orchestrator is not None
    assert build.count == 1


@pytest.mark.asyncio
async def test_a_read_after_an_input_changed_does_not_join_the_earlier_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache, harness_files, key, build = _gated_cache(tmp_path)
    before = asyncio.create_task(cache.get(key, harness_files, None, build))
    await asyncio.sleep(0)

    monkeypatch.setenv("VIBE_THEME", "changed-while-building")
    after = asyncio.create_task(cache.get(key, harness_files, None, build))
    await asyncio.sleep(0)
    assert build.release is not None
    build.release.set()
    await asyncio.gather(before, after)
    await cache.get(key, harness_files, None, build)

    assert build.count == 2
