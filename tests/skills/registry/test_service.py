from __future__ import annotations

import asyncio
from pathlib import Path
import threading
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from tests.conftest import build_test_vibe_config
from tests.skills.registry.conftest import make_item
from vibe.core.platform_auth import ApiKeyPlatformCredentialProvider
from vibe.core.skills.registry import (
    _manifest,
    _resolved,
    _service,
    _store,
    refresh_registry_skills,
)
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest
from vibe.core.skills.registry._service import RegistrySyncStatus
from vibe.core.utils import file_lock

_URL = "https://api.mistral.ai/v1/skills"


def _config(**kwargs: Any) -> Any:
    return SimpleNamespace(**kwargs)


def _endpoint(_config: Any, _credentials: Any = None) -> _service._Endpoint:
    return _service._Endpoint(
        "https://api.mistral.ai/v1",
        ApiKeyPlatformCredentialProvider.from_api_key("key"),
    )


def _catalog_page(
    skill_id: str, name: str, *, version: int = 1, latest: int = 1
) -> dict[str, Any]:
    return {
        "data": [
            {
                "skillId": skill_id,
                "skill": {
                    "skillName": name,
                    "skillDescription": "d",
                    "skillBody": "# B",
                },
                "version": version,
                "metadata": {"name": name, "latestVersion": latest},
            }
        ],
        "nextPageToken": "",
    }


def _skill_payload(skill_id: str, name: str, *, version: int = 1) -> dict[str, Any]:
    return {
        "skillId": skill_id,
        "skill": {
            "skillName": name,
            "skillDescription": "d",
            "skillBody": "# Body",
            "skillAssets": {},
        },
        "version": version,
        "metadata": {"name": name, "latestVersion": version},
    }


async def _pin(entry: ManifestEntry) -> None:
    await _manifest.save(
        _manifest.global_manifest_path(), SkillManifest(skills=[entry])
    )


@pytest.mark.asyncio
async def test_refresh_skipped_when_disabled() -> None:
    result = await refresh_registry_skills(
        _config(experimental_enable_registry_skills=False)
    )
    assert result.status is RegistrySyncStatus.SKIPPED


@pytest.mark.asyncio
async def test_refresh_skipped_without_endpoint() -> None:
    result = await refresh_registry_skills(
        _config(
            experimental_enable_registry_skills=True, get_mistral_provider=lambda: None
        )
    )
    assert result.status is RegistrySyncStatus.SKIPPED


@pytest.mark.asyncio
async def test_refresh_with_no_pins_has_nothing_to_fetch(
    respx_mock: respx.MockRouter,
) -> None:
    registry = respx_mock.route(url__startswith=_URL)

    result = await refresh_registry_skills(
        build_test_vibe_config(experimental_enable_registry_skills=True)
    )

    assert (result.status, result.written) == (RegistrySyncStatus.OK, 0)
    assert not registry.called


@pytest.mark.asyncio
async def test_refresh_with_every_frozen_pin_on_disk_never_reaches_the_registry(
    respx_mock: respx.MockRouter,
) -> None:
    registry = respx_mock.route(url__startswith=_URL)
    await _store.materialize(make_item(skill_id="sk", version=2), "frozen")
    await _pin(ManifestEntry(name="frozen", skill_id="sk", version=2))

    result = await refresh_registry_skills(
        build_test_vibe_config(experimental_enable_registry_skills=True)
    )

    assert (result.status, result.written) == (RegistrySyncStatus.OK, 0)
    assert not registry.called


@pytest.mark.asyncio
@respx.mock
async def test_refresh_materializes_frozen_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _pin(ManifestEntry(name="my-skill", skill_id="abc", version=1))
    respx.get(_URL).mock(
        return_value=httpx.Response(200, json=_catalog_page("abc", "my-skill"))
    )
    respx.get(f"{_URL}/abc").mock(
        return_value=httpx.Response(200, json=_skill_payload("abc", "my-skill"))
    )

    result = await refresh_registry_skills(
        _config(experimental_enable_registry_skills=True)
    )

    assert result.status is RegistrySyncStatus.OK
    assert result.written == 1
    assert await _store.is_materialized("abc", 1)


@pytest.mark.asyncio
@respx.mock
async def test_refresh_resolves_latest_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _pin(ManifestEntry(name="al", skill_id="xyz", version="latest"))
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, json=_catalog_page("xyz", "al", version=3, latest=3)
        )
    )
    respx.get(f"{_URL}/xyz").mock(
        return_value=httpx.Response(200, json=_skill_payload("xyz", "al", version=3))
    )

    result = await refresh_registry_skills(
        _config(experimental_enable_registry_skills=True)
    )

    assert result.status is RegistrySyncStatus.OK
    assert await _store.is_materialized("xyz", 3)


@pytest.mark.asyncio
@respx.mock
async def test_refresh_resolves_custom_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _pin(ManifestEntry(name="st", skill_id="cid", version="stable"))
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, json=_catalog_page("cid", "st", version=2, latest=2)
        )
    )
    # get_skill(alias="stable") resolves the alias to version 2.
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "st", version=2))
    )

    result = await refresh_registry_skills(
        _config(experimental_enable_registry_skills=True)
    )

    assert result.status is RegistrySyncStatus.OK
    assert await _store.is_materialized("cid", 2)
    assert _resolved.get("cid", "stable") == 2


@pytest.mark.asyncio
@respx.mock
async def test_refresh_fails_when_latest_lookup_unauthorized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _pin(ManifestEntry(name="reg", skill_id="cid", version="latest"))
    respx.get(f"{_URL}/cid").mock(return_value=httpx.Response(401))

    result = await refresh_registry_skills(
        _config(experimental_enable_registry_skills=True)
    )

    # A real registry failure (auth) must fail the sync loudly, not be swallowed.
    assert result.status is RegistrySyncStatus.FAILED


@pytest.mark.asyncio
@respx.mock
async def test_refresh_skips_latest_when_skill_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _pin(ManifestEntry(name="reg", skill_id="cid", version="latest"))
    respx.get(f"{_URL}/cid").mock(return_value=httpx.Response(404))

    result = await refresh_registry_skills(
        _config(experimental_enable_registry_skills=True)
    )

    # A skill deleted upstream (404) is skipped, not treated as a sync failure.
    assert result.status is RegistrySyncStatus.OK


@pytest.mark.asyncio
@respx.mock
async def test_refresh_alias_failure_keeps_cached_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An alias whose fetch fails keeps the version it last resolved to, still on disk."""
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    # Alias 'stable' last resolved to v1; a newer v2 also sits on disk.
    await _store.materialize(make_item(skill_id="cid", version=1), "st")
    await _store.materialize(make_item(skill_id="cid", version=2), "st")
    _resolved.record({("cid", "stable"): 1})
    await _pin(ManifestEntry(name="st", skill_id="cid", version="stable"))
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, json=_catalog_page("cid", "st", version=2, latest=2)
        )
    )
    respx.get(f"{_URL}/cid").mock(return_value=httpx.Response(500))

    await refresh_registry_skills(_config(experimental_enable_registry_skills=True))

    assert _resolved.get("cid", "stable") == 1
    assert await _store.is_materialized("cid", 1)


@pytest.mark.asyncio
@respx.mock
async def test_refresh_persists_alias_when_later_download_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A custom alias resolves + materializes inline; a later frozen/latest
    # download then fails. The alias mapping must already be persisted so
    # offline discovery finds the new body already sitting on disk.
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _pin(ManifestEntry(name="st", skill_id="cid", version="stable"))
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, json=_catalog_page("cid", "st", version=2, latest=2)
        )
    )
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "st", version=2))
    )

    async def _boom(*_args: Any, **_kwargs: Any) -> tuple[int, int]:
        raise OSError("disk full")

    monkeypatch.setattr(_service, "_materialize_missing", _boom)

    result = await refresh_registry_skills(
        _config(experimental_enable_registry_skills=True)
    )

    assert result.status is RegistrySyncStatus.FAILED
    # The alias body was materialized inline and its mapping persisted before
    # the crash, so discovery resolves 'stable' to the on-disk v2.
    assert await _store.is_materialized("cid", 2)
    assert _resolved.get("cid", "stable") == 2


@pytest.mark.asyncio
@respx.mock
async def test_refresh_persists_alias_when_body_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The alias resolves from the registry but its body is empty (materialize
    # returns None). The resolution must still be persisted so subsequent syncs
    # don't re-resolve it against the server; discovery keeps the prior body.
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _store.materialize(make_item(skill_id="cid", version=1), "st")
    await _pin(ManifestEntry(name="st", skill_id="cid", version="stable"))
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, json=_catalog_page("cid", "st", version=2, latest=2)
        )
    )
    empty = _skill_payload("cid", "st", version=2)
    empty["skill"]["skillBody"] = ""
    respx.get(f"{_URL}/cid").mock(return_value=httpx.Response(200, json=empty))

    await refresh_registry_skills(_config(experimental_enable_registry_skills=True))

    assert _resolved.get("cid", "stable") == 2
    assert not await _store.is_materialized("cid", 2)


def test_local_skill_dir_rejects_unsafe_names() -> None:
    # The name is untrusted registry metadata; it must not escape the skills
    # root when appended to it.
    for unsafe in ("../evil", "a/b", ".", "..", ""):
        assert _service._local_skill_dir(unsafe, _service.SkillScope.GLOBAL) is None
    safe = _service._local_skill_dir("good", _service.SkillScope.GLOBAL)
    assert safe is not None
    assert safe.name == "good"


def test_local_skill_dir_refuses_manifest_outside_project_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    (project / ".vibe").mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / ".vibe").mkdir(parents=True)
    monkeypatch.setattr(
        _service,
        "get_harness_files_manager",
        lambda: SimpleNamespace(project_roots=[project]),
    )

    inside = _service._local_skill_dir(
        "good", _service.SkillScope.PROJECT, project / ".vibe" / "skills.toml"
    )
    assert inside == project / ".vibe" / "skills" / "good"

    escaped = _service._local_skill_dir(
        "good", _service.SkillScope.PROJECT, outside / ".vibe" / "skills.toml"
    )
    assert escaped is None


def test_local_skill_dir_refuses_symlinked_vibe_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".vibe").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        _service,
        "get_harness_files_manager",
        lambda: SimpleNamespace(project_roots=[project]),
    )

    assert (
        _service._local_skill_dir(
            "good", _service.SkillScope.PROJECT, project / ".vibe" / "skills.toml"
        )
        is None
    )


@pytest.mark.asyncio
async def test_refresh_skips_unsafe_skill_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """A committed manifest with a path-traversal id is skipped, not a crash."""
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _pin(ManifestEntry(name="bad", skill_id="../evil", version=1))

    result = await refresh_registry_skills(
        _config(experimental_enable_registry_skills=True)
    )

    assert result.status is RegistrySyncStatus.OK


@pytest.mark.asyncio
@respx.mock
@pytest.mark.parametrize(
    ("pin", "answer", "status"),
    [
        (3, 503, RegistrySyncStatus.FAILED),
        ("stable", 503, RegistrySyncStatus.FAILED),
        (3, 404, RegistrySyncStatus.OK),
        ("stable", 404, RegistrySyncStatus.OK),
    ],
)
async def test_a_sync_that_could_not_fetch_a_pin_is_not_done(
    monkeypatch: pytest.MonkeyPatch,
    pin: int | str,
    answer: int,
    status: RegistrySyncStatus,
) -> None:
    await _pin(ManifestEntry(name="sk", skill_id="cid", version=pin))
    respx.get(f"{_URL}/cid").mock(return_value=httpx.Response(answer))

    result = await refresh_registry_skills(
        build_test_vibe_config(experimental_enable_registry_skills=True)
    )

    assert (result.status, result.written) == (status, 0)


@pytest.mark.asyncio
@respx.mock
async def test_refresh_failed_download_keeps_prior_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    # v1 on disk; 'latest' now points at v2, but the v2 download fails.
    await _store.materialize(make_item(skill_id="abc", version=1), "my-skill")
    await _pin(ManifestEntry(name="my-skill", skill_id="abc", version="latest"))
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, json=_catalog_page("abc", "my-skill", version=2, latest=2)
        )
    )
    respx.get(f"{_URL}/abc").mock(return_value=httpx.Response(500))

    await refresh_registry_skills(_config(experimental_enable_registry_skills=True))

    # The failed upgrade must not strip the still-usable v1.
    assert await _store.is_materialized("abc", 1)


@pytest.mark.asyncio
@respx.mock
async def test_a_refresh_keeps_the_version_it_moved_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _store.materialize(make_item(skill_id="abc", version=1), "my-skill")
    await _pin(ManifestEntry(name="my-skill", skill_id="abc", version="latest"))
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, json=_catalog_page("abc", "my-skill", version=2, latest=2)
        )
    )
    respx.get(f"{_URL}/abc").mock(
        return_value=httpx.Response(
            200, json=_skill_payload("abc", "my-skill", version=2)
        )
    )

    result = await refresh_registry_skills(
        build_test_vibe_config(experimental_enable_registry_skills=True)
    )

    assert (result.status, result.written) == (RegistrySyncStatus.OK, 1)
    assert await _store.is_materialized("abc", 2)
    assert await _store.is_materialized("abc", 1)


@pytest.mark.asyncio
@respx.mock
async def test_two_repositories_syncing_one_global_pin_at_once_both_succeed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    await _pin(ManifestEntry(name="my-skill", skill_id="abc", version="latest"))
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, json=_catalog_page("abc", "my-skill", version=2, latest=2)
        )
    )
    respx.get(f"{_URL}/abc").mock(
        return_value=httpx.Response(
            200, json=_skill_payload("abc", "my-skill", version=2)
        )
    )
    acquire = file_lock.acquire_file_lock
    second_waits = threading.Event()
    attempts: list[object] = []

    def acquire_counting(file: Any, *, blocking: bool = False) -> None:
        attempts.append(file)
        if len(attempts) == 2:
            second_waits.set()
        acquire(file, blocking=blocking)

    write_assets = _store._write_assets
    writes: list[Path] = []

    def write_while_the_second_waits(dest: Path, item: Any) -> None:
        write_assets(dest, item)
        writes.append(dest)
        assert second_waits.wait(timeout=10)

    monkeypatch.setattr(file_lock, "acquire_file_lock", acquire_counting)
    monkeypatch.setattr(_store, "_write_assets", write_while_the_second_waits)
    config = build_test_vibe_config(experimental_enable_registry_skills=True)

    results = await asyncio.gather(
        refresh_registry_skills(config, [tmp_path / "repo-a"]),
        refresh_registry_skills(config, [tmp_path / "repo-b"]),
    )

    assert [result.status for result in results] == [RegistrySyncStatus.OK] * 2
    assert len(writes) == 1
    version_dir = _store.skill_dir("abc", 2)
    assert sorted(entry.name for entry in version_dir.parent.iterdir()) == [
        ".2.lock",
        ".2.used",
        "2",
    ]
    assert await _store.is_materialized("abc", 2)


def _catalog_multi(*entries: tuple[str, str, int]) -> dict[str, Any]:
    """Catalog payload for several skills: each entry is (skill_id, name, latest)."""
    return {
        "data": [
            {
                "skillId": sid,
                "skill": {
                    "skillName": name,
                    "skillDescription": "d",
                    "skillBody": "# B",
                },
                "version": latest,
                "metadata": {"name": name, "latestVersion": latest},
            }
            for sid, name, latest in entries
        ],
        "nextPageToken": "",
    }


@pytest.mark.asyncio
@respx.mock
async def test_check_updates_filters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _manifest.save(
        _manifest.global_manifest_path(),
        SkillManifest(
            skills=[
                ManifestEntry(name="behind", skill_id="a", version=1),
                ManifestEntry(name="tracking", skill_id="b", version="latest"),
                ManifestEntry(name="current", skill_id="c", version=3),
            ]
        ),
    )
    respx.get(f"{_URL}/a").mock(
        return_value=httpx.Response(200, json=_skill_payload("a", "behind", version=2))
    )
    respx.get(f"{_URL}/c").mock(
        return_value=httpx.Response(200, json=_skill_payload("c", "current", version=3))
    )

    updates = await _service.check_updates(_config())

    # Only a frozen pin that is genuinely behind is surfaced: the alias pin
    # self-resolves and the up-to-date frozen pin has nothing newer.
    assert [(u.name, u.current_version, u.latest_version) for u in updates] == [
        ("behind", 1, 2)
    ]


@pytest.mark.asyncio
@respx.mock
async def test_check_updates_skips_unsafe_skill_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _manifest.save(
        _manifest.global_manifest_path(),
        SkillManifest(
            skills=[ManifestEntry(name="evil", skill_id="../escape", version=1)]
        ),
    )
    route = respx.get(url__startswith=_URL).mock(
        return_value=httpx.Response(200, json=_skill_payload("x", "evil", version=2))
    )

    updates = await _service.check_updates(_config())

    assert updates == []
    assert not route.called


@pytest.mark.asyncio
@respx.mock
async def test_check_new_versions_marks_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _pin(ManifestEntry(name="behind", skill_id="a", version=1))
    respx.get(f"{_URL}/a").mock(
        return_value=httpx.Response(200, json=_skill_payload("a", "behind", version=2))
    )

    first = await _service.check_new_versions(_config())
    assert [u.name for u in first] == ["behind"]

    # Already surfaced once: the second launch stays quiet for the same version.
    second = await _service.check_new_versions(_config())
    assert second == []


def test_local_skill_dir_refuses_symlinked_skills_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    (project / ".vibe").mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".vibe" / "skills").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        _service,
        "get_harness_files_manager",
        lambda: SimpleNamespace(project_roots=[project]),
    )

    assert (
        _service._local_skill_dir(
            "good", _service.SkillScope.PROJECT, project / ".vibe" / "skills.toml"
        )
        is None
    )


def test_manifest_path_for_scope_refuses_symlinked_vibe_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".vibe").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        _service,
        "get_harness_files_manager",
        lambda: SimpleNamespace(project_roots=[project]),
    )
    monkeypatch.setattr(
        _service._manifest,
        "project_manifest_paths_sync",
        lambda *_: [(project / ".vibe" / "skills.toml").resolve()],
    )

    with pytest.raises(_service.RegistrySkillsError):
        _service._manifest_path_for_scope(_service.SkillScope.PROJECT)


def test_manifest_path_for_scope_allows_real_project_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    (project / ".vibe").mkdir(parents=True)
    expected = (project / ".vibe" / "skills.toml").resolve()
    monkeypatch.setattr(
        _service,
        "get_harness_files_manager",
        lambda: SimpleNamespace(project_roots=[project]),
    )
    monkeypatch.setattr(
        _service._manifest, "project_manifest_paths_sync", lambda *_: [expected]
    )

    assert _service._manifest_path_for_scope(_service.SkillScope.PROJECT) == expected


def test_remove_skill_skips_a_symlinked_project_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".vibe").symlink_to(outside, target_is_directory=True)
    escaped = (project / ".vibe" / "skills.toml").resolve()
    monkeypatch.setattr(
        _service,
        "get_harness_files_manager",
        lambda: SimpleNamespace(project_roots=[project]),
    )
    monkeypatch.setattr(
        _service._manifest, "project_manifest_paths_sync", lambda *_: [escaped]
    )

    assert _service.remove_skill("sk", _service.SkillScope.PROJECT) is False
    assert not escaped.exists()
