from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os
from pathlib import Path

import httpx
import pytest
import respx

from tests.conftest import build_test_vibe_config
from tests.skills.registry.conftest import catalog_lists, clean_up, make_item
from tests.skills.registry.test_service import _URL, _catalog_page
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialProvider,
)
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.models import RegistryRef
from vibe.core.skills.registry import _eviction, _manifest, _resolved, _store
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest
from vibe.core.trusted_folders import trusted_folders_manager


def _stored_long_ago(
    skill_id: str, *versions: int, used_days_ago: int | None = 60
) -> None:
    """Written 45 days ago; ``used_days_ago`` None leaves them never marked used."""
    for version in versions:
        item = make_item(skill_id=skill_id, name="deploy", version=version)
        dest = _store._materialize(item, "deploy")
        assert dest is not None
        _age(dest, 45)
        marker = _store.used_marker(skill_id, version)
        if used_days_ago is None:
            marker.unlink(missing_ok=True)
            continue
        marker.touch()
        _age(marker, used_days_ago)


def _age(path: Path, days_ago: int) -> None:
    stamp = (datetime.now(UTC) - timedelta(days=days_ago)).timestamp()
    os.utime(path, (stamp, stamp))


@pytest.mark.asyncio
async def test_a_cleanup_keeps_what_the_manifests_pin_and_evicts_the_rest(
    respx_mock: respx.MockRouter,
) -> None:
    _stored_long_ago("cid", 0, 1, 2, 3)
    await _manifest.save(
        _manifest.global_manifest_path(),
        SkillManifest(
            skills=[
                ManifestEntry(name="sk", skill_id="cid", version=1),
                ManifestEntry(name="other", skill_id="cid", version="stable"),
            ]
        ),
    )
    _resolved.record({("cid", "stable"): 2})
    catalog_lists(respx_mock, cid=3)

    evicted = await clean_up()

    assert evicted == [("cid", 0)]
    assert [_store.is_materialized_sync("cid", v) for v in range(4)] == [
        False,
        True,
        True,
        True,
    ]


@pytest.mark.asyncio
async def test_a_cleanup_keeps_what_the_project_it_runs_for_pins(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    repo = tmp_path / "repo"
    _stored_long_ago("cid", 1, 2)
    await _manifest.save(
        repo / ".vibe" / "skills.toml",
        SkillManifest(skills=[ManifestEntry(name="deploy", skill_id="cid", version=1)]),
    )
    catalog_lists(respx_mock, cid=2)

    evicted = await clean_up([repo])

    assert evicted == []
    assert _store.is_materialized_sync("cid", 1)


@pytest.mark.asyncio
async def test_the_first_sighting_grace_keeps_an_unmarked_pin_of_another_repository(
    tmp_working_directory: Path, tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    repo_a, repo_b = tmp_working_directory, tmp_path / "repo-b"
    trusted_folders_manager.add_trusted(repo_a)
    _stored_long_ago("cid", 1, 2, used_days_ago=None)
    for root, version in ((repo_a, 1), (repo_b, "latest")):
        await _manifest.save(
            root / ".vibe" / "skills.toml",
            SkillManifest(
                skills=[ManifestEntry(name="deploy", skill_id="cid", version=version)]
            ),
        )

    catalog_lists(respx_mock, cid=2)

    evicted = await clean_up([repo_b])

    assert evicted == []
    assert _store.used_marker("cid", 1).is_file()
    config = build_test_vibe_config(experimental_enable_registry_skills=True)
    deploy = SkillManager(lambda: config).available_skills
    assert deploy["deploy"].registry == RegistryRef(skill_id="cid", version=1)


@pytest.mark.asyncio
async def test_a_cleanup_keeps_the_version_an_alias_of_another_repository_resolved_to(
    tmp_working_directory: Path, tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    repo_a, repo_b = tmp_path / "repo-a", tmp_working_directory
    trusted_folders_manager.add_trusted(repo_b)
    _stored_long_ago("cid", 1, 2, 3)
    await _manifest.save(
        repo_b / ".vibe" / "skills.toml",
        SkillManifest(
            skills=[ManifestEntry(name="deploy", skill_id="cid", version="stable")]
        ),
    )
    _resolved.record({("cid", "stable"): 1})
    catalog_lists(respx_mock, cid=3)

    evicted = await clean_up([repo_a])

    assert evicted == [("cid", 2)]
    config = build_test_vibe_config(experimental_enable_registry_skills=True)
    deploy = SkillManager(lambda: config).available_skills["deploy"]
    assert deploy.registry == RegistryRef(skill_id="cid", version=1, alias="stable")


@pytest.mark.asyncio
async def test_a_cleanup_within_a_day_of_the_last_one_does_nothing(
    respx_mock: respx.MockRouter,
) -> None:
    listing = catalog_lists(respx_mock, cid=2)
    await clean_up()
    _stored_long_ago("cid", 1, 2)

    assert await clean_up() == []
    assert listing.call_count == 0

    _age(_eviction.last_cleanup(), 1)

    assert await clean_up() == [("cid", 1)]
    assert listing.call_count == 1


@pytest.mark.asyncio
async def test_a_cleanup_keeps_what_the_registry_no_longer_serves(
    respx_mock: respx.MockRouter,
) -> None:
    _stored_long_ago("cid", 1, 2, 3, 4)
    _stored_long_ago("gone", 1, 2)
    catalog_lists(respx_mock, cid=2)

    evicted = await clean_up()

    assert evicted == [("cid", 1), ("cid", 2)]
    assert _store.is_materialized_sync("cid", 3)
    assert _store.is_materialized_sync("gone", 1)


@pytest.mark.asyncio
@respx.mock
async def test_one_catalog_listing_says_which_versions_the_registry_still_serves() -> (
    None
):
    _stored_long_ago("cid", 1, 2, 3, 4)
    _stored_long_ago("gone", 1, 2)
    _stored_long_ago("other", 1, 2, 3)
    first_page = _catalog_page("cid", "deploy", version=2, latest=2)
    first_page["nextPageToken"] = "p2"
    listing = respx.get(_URL).mock(
        side_effect=[
            httpx.Response(200, json=first_page),
            httpx.Response(200, json=_catalog_page("other", "ship", latest=5)),
        ]
    )

    evicted = await clean_up()

    assert evicted == [("cid", 1), ("cid", 2), ("other", 1), ("other", 2)]
    assert listing.call_count == 2
    assert _store.is_materialized_sync("cid", 3)
    assert _store.is_materialized_sync("gone", 1)


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    "answer",
    [httpx.Response(404), httpx.Response(500), httpx.ConnectTimeout("too slow")],
    ids=["missing", "failing", "timing-out"],
)
async def test_a_registry_that_cannot_answer_keeps_every_version(
    answer: httpx.Response | Exception,
) -> None:
    _stored_long_ago("cid", 1, 2)
    route = respx.get(_URL)
    if isinstance(answer, Exception):
        route.mock(side_effect=answer)
    else:
        route.mock(return_value=answer)

    assert await clean_up() == []
    assert _store.is_materialized_sync("cid", 1)


@pytest.mark.asyncio
async def test_a_signed_out_cleanup_keeps_every_version(
    monkeypatch: pytest.MonkeyPatch, respx_mock: respx.MockRouter
) -> None:
    _stored_long_ago("cid", 1, 2)
    listing = catalog_lists(respx_mock, cid=2)
    monkeypatch.delenv("MISTRAL_API_KEY")

    assert await clean_up() == []
    assert listing.call_count == 0
    assert _store.is_materialized_sync("cid", 1)


@pytest.mark.asyncio
async def test_the_served_check_carries_the_session_s_credentials(
    respx_mock: respx.MockRouter,
) -> None:
    def signed_in(_target: str) -> PlatformCredentialProvider:
        return ApiKeyPlatformCredentialProvider.from_api_key("session-key")

    _stored_long_ago("cid", 1, 2)
    listing = catalog_lists(respx_mock, cid=2)

    assert await clean_up(credentials=signed_in) == [("cid", 1)]
    assert listing.calls.last.request.headers["authorization"] == "Bearer session-key"
