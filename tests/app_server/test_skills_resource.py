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
async def test_catalog_reports_unauthenticated_instead_of_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vibe.app_server import _resources

    monkeypatch.setattr(_resources, "has_registry_endpoint", _no_endpoint, raising=True)
    config = build_test_vibe_config(experimental_enable_registry_skills=True)
    session = await create_test_app_server_session(build_test_agent_loop(config=config))
    try:
        result = await session.resources.skills.catalog()
        assert result.authenticated is False
        assert result.loaded is True
        assert result.skills == []
    finally:
        await session.close()
