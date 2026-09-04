from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx

from tests.skills.registry.conftest import make_item
from vibe.core.skills.registry import _ledger, _manifest, _resolved, _service, _store
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest
from vibe.core.skills.registry._service import (
    RegistrySyncStatus,
    refresh_registry_skills,
)

_URL = "https://api.mistral.ai/v1/skills"


def _config(**kwargs: Any) -> Any:
    return SimpleNamespace(**kwargs)


def _endpoint(_config: Any) -> _service._Endpoint:
    return _service._Endpoint("https://api.mistral.ai/v1", "key")


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
async def test_refresh_skipped_when_no_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    result = await refresh_registry_skills(
        _config(experimental_enable_registry_skills=True)
    )
    assert result.status is RegistrySyncStatus.SKIPPED


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

    # The alias's cached target (v1) stays; the unrelated newer v2 is pruned.
    assert await _store.is_materialized("cid", 1)
    assert not await _store.is_materialized("cid", 2)


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
async def test_convert_refuses_unsafe_name(monkeypatch: pytest.MonkeyPatch) -> None:
    # A pin with a path-traversal name must not let the export escape the root.
    await _store.materialize(make_item(skill_id="cid", version=1), "evil")
    await _manifest.save(
        _manifest.global_manifest_path(),
        SkillManifest(
            skills=[ManifestEntry(name="../evil", skill_id="cid", version=1)]
        ),
    )
    assert _service.convert_skill_to_local("../evil") is None


@pytest.mark.asyncio
async def test_convert_keeps_pin_when_export_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A failed export must not remove the registry pin, so the skill stays
    # usable and a retry is possible.
    await _store.materialize(make_item(skill_id="cid", version=1), "sk")
    await _pin(ManifestEntry(name="sk", skill_id="cid", version=1))

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(_store, "export_local_sync", _boom)
    with pytest.raises(OSError):
        _service.convert_skill_to_local("sk")

    assert _service._find_entry("sk", _service.SkillScope.GLOBAL) is not None


@pytest.mark.asyncio
async def test_refresh_skips_unsafe_skill_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    # A committed manifest with a path-traversal id must be skipped, not crash.
    await _pin(ManifestEntry(name="bad", skill_id="../evil", version=1))

    result = await refresh_registry_skills(
        _config(experimental_enable_registry_skills=True)
    )

    assert result.status is RegistrySyncStatus.SKIPPED


@pytest.mark.asyncio
async def test_refresh_fast_path_prunes_unpinned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    # Frozen pin v1 already materialized (so no network sync is needed), plus a
    # stale unpinned v2 that should still be pruned.
    await _store.materialize(make_item(skill_id="abc", version=1), "my-skill")
    await _store.materialize(make_item(skill_id="abc", version=2), "my-skill")
    await _pin(ManifestEntry(name="my-skill", skill_id="abc", version=1))

    result = await refresh_registry_skills(
        _config(experimental_enable_registry_skills=True)
    )

    assert result.status is RegistrySyncStatus.SKIPPED
    assert await _store.is_materialized("abc", 1)
    assert not await _store.is_materialized("abc", 2)


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
async def test_refresh_prunes_unpinned_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    # A stale version sits in the store but is not pinned.
    await _store.materialize(make_item(skill_id="abc", version=2), "my-skill")
    assert await _store.is_materialized("abc", 2)

    await _pin(ManifestEntry(name="my-skill", skill_id="abc", version=1))
    respx.get(_URL).mock(
        return_value=httpx.Response(200, json=_catalog_page("abc", "my-skill"))
    )
    respx.get(f"{_URL}/abc").mock(
        return_value=httpx.Response(200, json=_skill_payload("abc", "my-skill"))
    )

    await refresh_registry_skills(_config(experimental_enable_registry_skills=True))

    assert await _store.is_materialized("abc", 1)
    assert not await _store.is_materialized("abc", 2)


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
async def test_import_skill_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=3))
    )

    entry = await _service.import_skill(_config(), "cid", version=3)

    assert entry.version == 3
    manifest = _manifest.load_sync(_manifest.global_manifest_path())
    assert any(e.name == "sk" and e.version == 3 for e in manifest.skills)
    assert await _store.is_materialized("cid", 3)


@pytest.mark.asyncio
@respx.mock
async def test_import_skill_latest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=4))
    )

    entry = await _service.import_skill(_config(), "cid")

    assert entry.version == "latest"
    manifest = _manifest.load_sync(_manifest.global_manifest_path())
    assert any(e.name == "sk" and e.version == "latest" for e in manifest.skills)


@pytest.mark.asyncio
@respx.mock
async def test_import_skill_custom_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=5))
    )

    entry = await _service.import_skill(_config(), "cid", alias="stable")

    assert entry.version == "stable"
    # A custom alias records where it resolved so discovery can find it offline.
    assert _resolved.get("cid", "stable") == 5


@pytest.mark.asyncio
@respx.mock
async def test_set_skill_version_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _pin(ManifestEntry(name="sk", skill_id="cid", version="latest"))
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=1))
    )

    entry = await _service.set_skill_version(_config(), "sk", 1)

    assert entry is not None and entry.version == 1
    manifest = _manifest.load_sync(_manifest.global_manifest_path())
    assert any(e.name == "sk" and e.version == 1 for e in manifest.skills)


@pytest.mark.asyncio
@respx.mock
async def test_set_skill_latest_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _pin(ManifestEntry(name="sk", skill_id="cid", version=1))
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=2))
    )

    entry = await _service.set_skill_latest(_config(), "sk")

    assert entry is not None and entry.version == "latest"


@pytest.mark.asyncio
@respx.mock
async def test_set_skill_alias_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _pin(ManifestEntry(name="sk", skill_id="cid", version=1))
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=7))
    )

    entry = await _service.set_skill_alias(_config(), "sk", "beta")

    assert entry is not None and entry.version == "beta"
    assert _resolved.get("cid", "beta") == 7


@pytest.mark.asyncio
async def test_set_skill_version_unknown_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    assert await _service.set_skill_version(_config(), "missing", 1) is None


@pytest.mark.asyncio
async def test_remove_skill_one_scope_only() -> None:
    await _pin(ManifestEntry(name="sk", skill_id="cid", version=1))

    # Removing the project scope (no project pin) leaves the global pin intact.
    assert _service.remove_skill("sk", _service.SkillScope.PROJECT) is False
    global_path = _manifest.global_manifest_path()
    assert any(e.name == "sk" for e in _manifest.load_sync(global_path).skills)

    # Removing the global scope drops it.
    assert _service.remove_skill("sk", _service.SkillScope.GLOBAL) is True
    assert not any(e.name == "sk" for e in _manifest.load_sync(global_path).skills)


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


@pytest.mark.asyncio
@respx.mock
async def test_import_project_scope_uses_given_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Project-scoped import must target the caller's (session) roots, not the
    # global harness singleton.
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    root = tmp_path / "proj"
    root.mkdir()
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=1))
    )

    entry = await _service.import_skill(
        _config(), "cid", scope=_service.SkillScope.PROJECT, roots=[root]
    )

    assert entry.name == "sk"
    manifest_path = root / ".vibe" / "skills.toml"
    assert manifest_path.is_file()
    manifest = _manifest.load_sync(manifest_path)
    assert any(e.name == "sk" for e in manifest.skills)
    # The global manifest is untouched.
    global_manifest = _manifest.load_sync(_manifest.global_manifest_path())
    assert not any(e.name == "sk" for e in global_manifest.skills)


def test_ledger_union_across_repos() -> None:
    _ledger.record("repo-a", {("cid", 1)})
    _ledger.record("repo-b", {("cid", 2), ("other", 5)})
    assert _ledger.union() == {("cid", 1), ("cid", 2), ("other", 5)}
    # Re-recording a repo replaces only its own entry.
    _ledger.record("repo-a", {("cid", 3)})
    assert _ledger.union() == {("cid", 3), ("cid", 2), ("other", 5)}


def test_ledger_keeps_each_repo_in_its_own_file() -> None:
    _ledger.record("repo-a", {("cid", 1)})
    _ledger.record("repo-b", {("cid", 2)})

    files = sorted(p.name for p in _ledger.ledger_root().glob("*.txt"))
    assert files == ["repo-a.txt", "repo-b.txt"]


def test_ledger_concurrent_writers_keep_both_entries() -> None:
    from concurrent.futures import ThreadPoolExecutor

    payloads = {f"repo-{n}": {(f"cid{n}", n)} for n in range(12)}
    with ThreadPoolExecutor(max_workers=12) as pool:
        list(pool.map(lambda kv: _ledger.record(*kv), payloads.items()))

    assert _ledger.union() == {entry for pins in payloads.values() for entry in pins}


@pytest.mark.asyncio
@respx.mock
async def test_refresh_keeps_sibling_repo_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A sibling repo (different project roots) pins cid@2 in the shared store;
    # this repo pins cid@1. Syncing this repo must not evict the sibling's cid@2.
    monkeypatch.setattr(_service, "_resolve_endpoint", _endpoint)
    await _store.materialize(make_item(skill_id="cid", version=1), "sk")
    await _store.materialize(make_item(skill_id="cid", version=2), "sk")
    _ledger.record("sibling-repo", {("cid", 2)})
    await _pin(ManifestEntry(name="sk", skill_id="cid", version=1))
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, json=_catalog_page("cid", "sk", version=1, latest=1)
        )
    )
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=1))
    )

    await refresh_registry_skills(_config(experimental_enable_registry_skills=True))

    assert await _store.is_materialized("cid", 1)  # this repo's pin
    assert await _store.is_materialized("cid", 2)  # sibling repo's pin preserved


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


def test_ledger_drops_an_entry_when_a_repo_stops_pinning() -> None:
    _ledger.record("repo-a", {("cid", 1)})
    _ledger.record("repo-b", {("other", 2)})
    assert _ledger.union() == {("cid", 1), ("other", 2)}

    _ledger.record("repo-a", set())

    assert _ledger.union() == {("other", 2)}
    assert not (_ledger.ledger_root() / "repo-a.txt").exists()


def test_ledger_keeps_a_project_claim_on_a_globally_pinned_version() -> None:
    _ledger.record(_ledger.GLOBAL_KEY, {("cid", 1)})
    _ledger.record("repo-a", {("cid", 1)})
    assert _ledger.union() == {("cid", 1)}

    _ledger.record(_ledger.GLOBAL_KEY, set())

    assert _ledger.union() == {("cid", 1)}


@pytest.mark.asyncio
async def test_prune_keeps_a_version_claimed_during_the_sweep() -> None:
    await _store.materialize(make_item(skill_id="cid", version=1), "body")
    await _store.materialize(make_item(skill_id="cid", version=2), "body")

    await _store.prune({("cid", 2)}, recheck=lambda: {("cid", 1)})

    assert await _store.is_materialized("cid", 1)
    assert await _store.is_materialized("cid", 2)


@pytest.mark.asyncio
async def test_prune_still_drops_an_unclaimed_version() -> None:
    await _store.materialize(make_item(skill_id="cid", version=1), "body")
    await _store.materialize(make_item(skill_id="cid", version=2), "body")

    await _store.prune({("cid", 2)}, recheck=set)

    assert not await _store.is_materialized("cid", 1)
    assert await _store.is_materialized("cid", 2)


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
