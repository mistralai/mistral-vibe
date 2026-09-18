from __future__ import annotations

import pytest

from tests.conftest import build_test_agent_loop, build_test_vibe_config
from tests.skills.registry.conftest import make_item
from tests.stubs.app_server import create_test_app_server_session
from vibe.core.skills.registry import _manifest, _store
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest


async def _no_endpoint(_config: object) -> bool:
    return False


@pytest.mark.asyncio
async def test_skills_installed_projects_registry_pin() -> None:
    await _store.materialize(
        make_item(skill_id="cid", name="reg-skill", version=1), "reg-skill"
    )
    await _manifest.save(
        _manifest.global_manifest_path(),
        SkillManifest(
            skills=[ManifestEntry(name="reg-skill", skill_id="cid", version=1)]
        ),
    )
    config = build_test_vibe_config(experimental_enable_registry_skills=True)
    session = await create_test_app_server_session(build_test_agent_loop(config=config))
    try:
        installed = {s.name: s for s in await session.resources.skills.read_installed()}
        assert "reg-skill" in installed
        info = installed["reg-skill"]
        assert info.source == "registry"
        assert info.registry is not None
        assert info.registry.skill_id == "cid"
        assert info.registry.version == 1
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_set_enabled_toggles_disabled_skills() -> None:
    await _store.materialize(
        make_item(skill_id="cid", name="reg-skill", version=1), "reg-skill"
    )
    await _manifest.save(
        _manifest.global_manifest_path(),
        SkillManifest(
            skills=[ManifestEntry(name="reg-skill", skill_id="cid", version=1)]
        ),
    )
    config = build_test_vibe_config(experimental_enable_registry_skills=True)
    session = await create_test_app_server_session(build_test_agent_loop(config=config))
    try:
        skills = session.resources.skills

        installed = await skills.set_enabled("reg-skill", False)
        by_name = {s.name: s for s in installed}
        assert by_name["reg-skill"].enabled is False

        installed = await skills.set_enabled("reg-skill", True)
        by_name = {s.name: s for s in installed}
        assert by_name["reg-skill"].enabled is True
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_allowlisted_out_skill_is_not_reported_enabled() -> None:
    await _store.materialize(
        make_item(skill_id="cid", name="reg-skill", version=1), "reg-skill"
    )
    await _manifest.save(
        _manifest.global_manifest_path(),
        SkillManifest(
            skills=[ManifestEntry(name="reg-skill", skill_id="cid", version=1)]
        ),
    )
    config = build_test_vibe_config(
        experimental_enable_registry_skills=True, enabled_skills=["something-else"]
    )
    session = await create_test_app_server_session(build_test_agent_loop(config=config))
    try:
        installed = {s.name: s for s in await session.resources.skills.read_installed()}
        info = installed["reg-skill"]
        assert info.enabled is False
        assert info.locked is True
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_catalog_reports_unauthenticated_instead_of_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibe.app_server import _skills_service

    monkeypatch.setattr(
        _skills_service, "has_registry_endpoint", _no_endpoint, raising=True
    )
    config = build_test_vibe_config(experimental_enable_registry_skills=True)
    session = await create_test_app_server_session(build_test_agent_loop(config=config))
    try:
        result = await session.resources.skills.catalog()
        assert result.authenticated is False
        assert result.loaded is True
        assert result.skills == []
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_set_enabled_refuses_a_name_that_is_not_installed() -> None:
    """Prepare a session with one installed skill.

    Do call ``skills/setEnabled`` for a name that does not exist.

    Assert it is refused. A typo from any client would otherwise be written
    permanently into the user's config, removable only by hand-editing.
    """
    config = build_test_vibe_config(experimental_enable_registry_skills=True)
    session = await create_test_app_server_session(build_test_agent_loop(config=config))
    try:
        with pytest.raises(Exception, match="no installed skill named"):
            await session.resources.skills.set_enabled("does-not-exist", False)
    finally:
        await session.close()


@pytest.mark.asyncio
async def test_set_enabled_refuses_a_skill_a_config_pattern_holds() -> None:
    """Prepare a skill held off by an ``enabled_skills`` allowlist.

    Do call ``skills/setEnabled`` for it.

    Assert it is refused rather than silently writing a name the allowlist will
    keep overriding. The browser already hides the verb; the server is the
    shared entry point and has to agree.
    """
    await _store.materialize(
        make_item(skill_id="cid", name="reg-skill", version=1), "reg-skill"
    )
    await _manifest.save(
        _manifest.global_manifest_path(),
        SkillManifest(
            skills=[ManifestEntry(name="reg-skill", skill_id="cid", version=1)]
        ),
    )
    config = build_test_vibe_config(
        experimental_enable_registry_skills=True, enabled_skills=["something-else"]
    )
    session = await create_test_app_server_session(build_test_agent_loop(config=config))
    try:
        with pytest.raises(Exception, match="fixed by configuration"):
            await session.resources.skills.set_enabled("reg-skill", True)
    finally:
        await session.close()
