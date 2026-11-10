from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import aclosing
import logging
from pathlib import Path
import sys

import pytest
import respx

pytest.importorskip("mistralai_vibe_local_harness.vibe")

from tests.app_server.test_sandbox_skills import (
    _TASK,
    _copies,
    _host_skill,
    _options,
    _registry_skill,
    _write_skill,
)
from tests.conftest import build_test_vibe_config
from tests.skills.registry.conftest import (
    make_item,
    pin_deploy,
    registry_serves_deploy,
    registry_stalls,
    store_deploy,
    turn_registry_skills_on,
)
from tests.stubs.local_sandbox import (
    EXECUTE_COMPLETION,
    RecordingSandbox,
    ScriptedModel,
    build_trusted_project,
)
from vibe.app_server._sandbox_skills import SandboxSessionSkills, SandboxSkills
from vibe.app_server._skills import discover_session_skills
from vibe.app_server.events import CallbackRequested
from vibe.app_server.local import LocalHarnessHost
from vibe.app_server.protocol import SkillsSetEnabledParams
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.models import InstalledSkill, RegistryRef, SkillInfo, SkillScope
from vibe.core.skills.registry import _manifest, _store
from vibe.core.skills.registry._eviction import last_cleanup
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest

pytestmark = [
    pytest.mark.timeout(60),
    pytest.mark.skipif(
        sys.platform == "win32", reason="the sandbox runs POSIX command lines"
    ),
]


@pytest.fixture
def sandbox_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The sandbox's temporary directory, where skills are copied."""
    path = tmp_path / "sandbox-tmp"
    path.mkdir()
    monkeypatch.setenv("TMPDIR", str(path))
    return path


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return build_trusted_project(tmp_path / "project", monkeypatch)


async def _holding(project: Path, *copies: SkillInfo) -> SandboxSessionSkills:
    return await SandboxSkills(RecordingSandbox(project)).prepare_session(
        copies, roots=[]
    )


async def _copied(
    project: Path, store: Path, versions: Sequence[int]
) -> SandboxSessionSkills:
    return await _holding(
        project,
        *(
            _registry_skill(store / "cid" / str(version), "deploy", version=version)
            for version in versions
        ),
    )


@pytest.mark.asyncio
async def test_a_sandbox_keeps_its_newest_older_copy_when_a_newer_version_is_fetched(
    tmp_path: Path, project: Path, sandbox_tmp: Path, caplog: pytest.LogCaptureFixture
) -> None:
    session = await _copied(project, tmp_path / "store", [1, 2, 4])
    moved = _registry_skill(tmp_path / "store" / "cid" / "3", "deploy", version=3)

    with caplog.at_level(logging.WARNING):
        kept = session.copied_version(moved)
        again = session.copied_version(moved)

    assert session.locate(moved) is None
    assert kept.registry == RegistryRef(skill_id="cid", version=2, alias="latest")
    assert again == kept
    assert session.locate(kept) is not None
    assert [r.getMessage() for r in caplog.records if "Kept" in r.getMessage()] == [
        "Kept v2 of skill 'deploy': v3 has no copy in the sandbox"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("copies", "pinned"),
    [([1, 3], 2), ([1], 3)],
    ids=["pinned-between-two-copies", "upgraded-past-its-only-copy"],
)
async def test_a_pin_on_a_version_number_with_no_copy_is_not_given_an_older_one(
    tmp_path: Path, project: Path, sandbox_tmp: Path, copies: list[int], pinned: int
) -> None:
    session = await _copied(project, tmp_path / "store", copies)
    chosen = _registry_skill(
        tmp_path / "store" / "cid" / str(pinned), "deploy", version=pinned, alias=None
    )

    kept = session.copied_version(chosen)

    assert kept is chosen
    assert session.locate(kept) is None


@pytest.mark.asyncio
async def test_a_pin_on_an_older_version_than_the_copy_is_not_given_the_copy(
    tmp_path: Path, project: Path, sandbox_tmp: Path
) -> None:
    session = await _copied(project, tmp_path / "store", [4])
    pinned = _registry_skill(
        tmp_path / "store" / "cid" / "3", "deploy", version=3, alias="stable"
    )

    kept = session.copied_version(pinned)

    assert kept is pinned
    assert session.locate(kept) is None


@pytest.mark.asyncio
async def test_a_skill_with_its_own_copy_or_outside_the_registry_is_kept_as_it_is(
    tmp_path: Path, project: Path, sandbox_tmp: Path
) -> None:
    older = _registry_skill(tmp_path / "cid" / "1", "deploy", version=1)
    pinned = _registry_skill(tmp_path / "cid" / "2", "deploy", version=2)
    local = _host_skill(tmp_path / "local", "notes", files={})
    session = await _holding(project, older, pinned)

    assert session.copied_version(pinned) is pinned
    assert session.copied_version(local) is local


@pytest.mark.asyncio
async def test_a_shared_skill_with_no_copy_at_all_is_left_to_be_dropped(
    tmp_path: Path, project: Path, sandbox_tmp: Path
) -> None:
    pinned = _registry_skill(tmp_path / "cid" / "2", "deploy", version=2)
    session = await _holding(project)

    assert session.copied_version(pinned) is pinned


@pytest.mark.asyncio
async def test_a_kept_copy_stands_in_as_the_pin_it_replaces(
    tmp_path: Path, project: Path, sandbox_tmp: Path
) -> None:
    pinned = _registry_skill(
        tmp_path / "cid" / "2", "ship", version=2, alias="stable"
    ).model_copy(update={"scope": SkillScope.PROJECT})
    older = _registry_skill(tmp_path / "cid" / "1", "deploy", version=1)
    session = await _holding(project, older)

    kept = session.copied_version(pinned)

    assert (kept.name, kept.scope, kept.skill_path) == (
        "ship",
        SkillScope.PROJECT,
        older.skill_path,
    )
    assert kept.registry == RegistryRef(skill_id="cid", version=1, alias="stable")


@pytest.mark.asyncio
async def test_only_an_active_pin_is_listed_in_the_version_the_sandbox_keeps(
    tmp_path: Path, project: Path, sandbox_tmp: Path, caplog: pytest.LogCaptureFixture
) -> None:
    newer = _registry_skill(tmp_path / "cid" / "2", "deploy", version=2)
    pins = [
        InstalledSkill(newer.model_copy(update={"scope": SkillScope.PROJECT}), True),
        InstalledSkill(newer, False),
    ]
    older = _registry_skill(tmp_path / "cid" / "1", "deploy", version=1)
    session = await _holding(project, older)

    with caplog.at_level(logging.WARNING):
        listed = session.installed(pins)

    assert [
        (pin.info.scope, pin.info.registry.version, pin.active)
        for pin in listed
        if pin.info.registry is not None
    ] == [(SkillScope.PROJECT, 1, True), (SkillScope.GLOBAL, 2, False)]
    assert not [r for r in caplog.records if "Kept" in r.getMessage()]


@pytest.mark.asyncio
async def test_a_sandboxed_session_keeps_its_copy_when_its_pin_moves_to_a_new_version(
    tmp_path: Path, project: Path, sandbox_tmp: Path
) -> None:
    config = build_test_vibe_config(experimental_enable_registry_skills=True)
    files = HarnessFilesManager(sources=(), cwd=tmp_path)
    _manifest.save_sync(
        _manifest.global_manifest_path(),
        SkillManifest(skills=[ManifestEntry(name="deploy", skill_id="cid")]),
    )

    def stored(version: int) -> SkillInfo:
        _store._materialize(make_item(skill_id="cid", version=version), "deploy")
        manager = SkillManager(lambda: config, harness_files=files)
        return manager.available_skills["deploy"]

    copy = stored(1)
    stored(2)
    sandbox = await _holding(project, copy)

    _, projection = discover_session_skills(
        lambda: config,
        harness_files=files,
        plugin_skills={},
        plugin_contexts=[],
        skill_tool_available=True,
        sandbox=sandbox,
    )

    location = sandbox.locate(copy)
    assert location is not None
    assert [(skill.name, skill.path) for skill in projection.definitions] == [
        ("deploy", location.path)
    ]
    assert projection.registry_versions == {"deploy": ("cid", 1)}


def _copied_bodies(sandbox_tmp: Path) -> list[bytes]:
    return [copy.get("SKILL.md", b"") for copy in _copies(sandbox_tmp).values()]


def _without_registry_sync(respx_mock: respx.MockRouter) -> None:
    """Keep the registry from answering, so a session's sync, which asks it for the
    newest version of the pin tracking latest, never ends and never derives the session
    again behind the test's back.
    """
    registry_stalls(respx_mock)


@pytest.mark.asyncio
async def test_skills_lists_a_pin_at_the_version_its_sandboxed_session_keeps(
    project: Path, sandbox_tmp: Path, config_dir: Path, respx_mock: respx.MockRouter
) -> None:
    _without_registry_sync(respx_mock)
    turn_registry_skills_on(config_dir)
    await pin_deploy()
    await store_deploy(1)
    host = LocalHarnessHost()
    session = await host.start(_options(RecordingSandbox(project)))

    try:
        await store_deploy(2)
        listed = await session.resources.skills.read_installed()
    finally:
        await session.close()
        await host.close()

    (row,) = [row for row in listed if row.name == "deploy"]
    assert row.registry is not None
    assert (row.active, row.registry.version) == (True, 1)
    assert "Deploy with v1." in row.prompt


@pytest.mark.asyncio
async def test_a_skill_change_copies_the_version_a_pin_now_loads_into_the_sandbox(
    project: Path, sandbox_tmp: Path, config_dir: Path, respx_mock: respx.MockRouter
) -> None:
    _without_registry_sync(respx_mock)
    turn_registry_skills_on(config_dir)
    _write_skill(project / ".agents" / "skills" / "release", "release", "Release.")
    await pin_deploy()
    await store_deploy(1)
    host = LocalHarnessHost()
    session = await host.start(_options(RecordingSandbox(project)))

    try:
        await store_deploy(2)
        await session.resources.skills._mutate(
            "skills/setEnabled",
            SkillsSetEnabledParams(
                session_id=session.session_id, name="release", enabled=False
            ),
        )
        running = session.resources.runtime.skills
    finally:
        await session.close()
        await host.close()

    (deploy,) = [skill for skill in running if skill.name == "deploy"]
    assert deploy.registry is not None
    assert deploy.registry.version == 2
    assert any(b"Deploy with v2." in body for body in _copied_bodies(sandbox_tmp))


@pytest.mark.asyncio
async def test_a_sandboxed_session_fetches_a_missing_pin_and_copies_it_in(
    project: Path,
    sandbox_tmp: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    respx_mock: respx.MockRouter,
) -> None:
    turn_registry_skills_on(config_dir)
    await pin_deploy()
    registry_serves_deploy(respx_mock)
    model = ScriptedModel({_TASK: [("skill", {"name": "deploy"})]})
    monkeypatch.setattr(EXECUTE_COMPLETION, model)
    host = LocalHarnessHost()
    session = await host.start(_options(RecordingSandbox(project)))

    try:
        for _ in range(200):
            if any(b"Deploy with v1." in body for body in _copied_bodies(sandbox_tmp)):
                break
            await asyncio.sleep(0.05)
        async with aclosing(session.act(_TASK)) as events:
            async for event in events:
                if isinstance(event, CallbackRequested):
                    await session.deny_callback(event.callback)
    finally:
        await session.close()
        await host.close()

    assert any(b"Deploy with v1." in body for body in _copied_bodies(sandbox_tmp))
    assert "Deploy with v1." in model.final_input(_TASK)


@pytest.mark.asyncio
async def test_an_agent_switch_copies_the_version_a_pin_now_loads_into_the_sandbox(
    project: Path, sandbox_tmp: Path, config_dir: Path, respx_mock: respx.MockRouter
) -> None:
    _without_registry_sync(respx_mock)
    turn_registry_skills_on(config_dir)
    await pin_deploy()
    await store_deploy(1)
    host = LocalHarnessHost()
    session = await host.start(_options(RecordingSandbox(project)))

    try:
        await store_deploy(2)
        await session.resources.agents.switch(BuiltinAgentName.ASK)
        running = session.resources.runtime.skills
    finally:
        await session.close()
        await host.close()

    (deploy,) = [skill for skill in running if skill.name == "deploy"]
    assert deploy.registry is not None
    assert deploy.registry.version == 2
    assert any(b"Deploy with v2." in body for body in _copied_bodies(sandbox_tmp))


@pytest.mark.asyncio
async def test_a_pin_its_sandbox_cannot_copy_is_reported_by_the_next_derivation(
    project: Path, sandbox_tmp: Path, config_dir: Path, respx_mock: respx.MockRouter
) -> None:
    _without_registry_sync(respx_mock)
    turn_registry_skills_on(config_dir)
    await pin_deploy()
    host = LocalHarnessHost()
    session = await host.start(_options(RecordingSandbox(project)))

    try:
        await store_deploy(1)
        large = _store.skill_dir("cid", 1) / "data.bin"
        large.write_bytes(b"x" * (257 * 1024))
        await session.resources.agents.switch(BuiltinAgentName.ASK)
        issues = session.resources.runtime.issues
    finally:
        await session.close()
        await host.close()

    (issue,) = [issue for issue in issues if issue.file.endswith("SKILL.md")]
    assert issue.file == str(_store.skill_dir("cid", 1) / "SKILL.md")
    assert "data.bin exceeds the size limit" in issue.message


async def _sync_over() -> None:
    """A sync claims the day's store cleanup only after its check of the session's pins,
    and any derivation that check asked for, is over.
    """
    for _ in range(600):
        if last_cleanup().exists():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the registry sync never reached the store cleanup")


@pytest.mark.asyncio
async def test_a_project_skill_named_like_a_pin_derives_no_sandboxed_session_again(
    project: Path, sandbox_tmp: Path, config_dir: Path, respx_mock: respx.MockRouter
) -> None:
    turn_registry_skills_on(config_dir)
    _write_skill(project / ".agents" / "skills" / "deploy", "deploy", "Our deploy.")
    await pin_deploy()
    await store_deploy(1)
    registry_serves_deploy(respx_mock, latest=2)
    host = LocalHarnessHost()
    session = await host.start(_options(RecordingSandbox(project)))

    try:
        await _sync_over()
        running = session.resources.runtime.skills
    finally:
        await session.close()
        await host.close()

    (deploy,) = [skill for skill in running if skill.name == "deploy"]
    assert deploy.registry is None
    assert _store.is_materialized_sync("cid", 2)
    assert not any(b"Deploy with v2." in body for body in _copied_bodies(sandbox_tmp))


@pytest.mark.asyncio
async def test_with_registry_skills_off_a_session_copies_skills_only_as_it_opens(
    project: Path, sandbox_tmp: Path, config_dir: Path
) -> None:
    _write_skill(project / ".agents" / "skills" / "release", "release", "Release.")
    host = LocalHarnessHost()
    session = await host.start(_options(RecordingSandbox(project)))

    try:
        _write_skill(config_dir / "skills" / "notes", "notes", "Late notes.")
        await session.resources.skills._mutate(
            "skills/setEnabled",
            SkillsSetEnabledParams(
                session_id=session.session_id, name="release", enabled=False
            ),
        )
    finally:
        await session.close()
        await host.close()

    assert not any(b"Late notes." in body for body in _copied_bodies(sandbox_tmp))
