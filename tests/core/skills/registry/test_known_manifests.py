from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from tests.conftest import build_test_vibe_config
from tests.skills.registry.conftest import catalog_lists, clean_up, make_item
from vibe.core.skills.models import SkillScope
from vibe.core.skills.registry import _known_manifests, _manifest, _service, _store
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest

_URL = "https://api.mistral.ai/v1/skills"


def _config() -> Any:
    return build_test_vibe_config(experimental_enable_registry_skills=True)


async def _pins(root: Path, version: int) -> Path:
    path = root / ".vibe" / "skills.toml"
    await _manifest.save(
        path,
        SkillManifest(
            skills=[ManifestEntry(name="deploy", skill_id="cid", version=version)]
        ),
    )
    return path


def _stored_and_unused(*versions: int) -> None:
    long_ago = (datetime.now(UTC) - timedelta(days=40)).timestamp()
    for version in versions:
        assert _store._materialize(make_item(skill_id="cid", version=version), "sk")
        os.utime(_store.used_marker("cid", version), (long_ago, long_ago))


@pytest.mark.asyncio
async def test_a_cleanup_keeps_what_a_project_a_session_read_pins(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    repo_a, repo_b = tmp_path / "repo-a", tmp_path / "repo-b"
    manifest = await _pins(repo_a, 1)
    _stored_and_unused(1, 2)

    await _service.refresh_registry_skills(_config(), [repo_a])
    catalog_lists(respx_mock, cid=2)
    evicted = await clean_up([repo_b])

    assert _known_manifests.remembered() == [manifest.resolve()]
    assert evicted == []
    assert _store.is_materialized_sync("cid", 1)


@pytest.mark.asyncio
@respx.mock
async def test_pinning_into_a_project_puts_its_manifest_on_record(
    tmp_path: Path,
) -> None:
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(
            200,
            json={
                "skillId": "cid",
                "skill": {"skillBody": "# Body", "skillName": "sk"},
                "version": 1,
                "metadata": {"name": "sk", "latestVersion": 1},
            },
        )
    )
    repo = tmp_path / "repo"
    repo.mkdir()

    await _service.import_skill(
        _config(), "cid", version=1, scope=SkillScope.PROJECT, roots=[repo]
    )

    assert _known_manifests.remembered() == [(repo / ".vibe" / "skills.toml").resolve()]


@pytest.mark.asyncio
async def test_a_manifest_gone_from_disk_drops_off_the_record(tmp_path: Path) -> None:
    gone = await _pins(tmp_path / "gone", 1)
    kept = await _pins(tmp_path / "kept", 1)
    new = await _pins(tmp_path / "new", 1)
    _known_manifests.remember([gone, kept])
    gone.unlink()

    assert _known_manifests.remembered() == [kept.resolve()]

    _known_manifests.remember([new])

    assert _known_manifests._read() == {str(kept.resolve()), str(new.resolve())}


@pytest.mark.asyncio
async def test_a_record_that_cannot_be_read_stops_the_cleanup_and_stays(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    _stored_and_unused(1, 2)
    record = _known_manifests.record_path()
    record.write_text("{not json", encoding="utf-8")

    _known_manifests.remember([await _pins(tmp_path / "repo", 2)])
    catalog_lists(respx_mock, cid=2)
    with pytest.raises(ValueError):
        await clean_up()

    assert record.read_text(encoding="utf-8") == "{not json"
    assert _store.is_materialized_sync("cid", 1)
