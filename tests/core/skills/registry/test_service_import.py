from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from tests.conftest import build_test_vibe_config
from tests.skills.registry.conftest import held_by_another_process, make_item
from tests.skills.registry.test_service import (
    _URL,
    _config,
    _endpoint,
    _pin,
    _skill_payload,
)
from vibe.core.skills.manager import SkillManager
from vibe.core.skills.parser import parse_skill_markdown
from vibe.core.skills.registry import _manifest, _resolved, _service, _store
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest


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
async def test_converting_a_pin_renamed_upstream_keeps_the_pin_s_name() -> None:
    await _store.materialize(make_item(skill_id="cid", name="foo", version=1), "foo")
    await _pin(ManifestEntry(name="bar", skill_id="cid", version=1))

    target = _service.convert_skill_to_local("bar")

    assert target is not None
    frontmatter, _ = parse_skill_markdown((target / "SKILL.md").read_text())
    assert (target.name, frontmatter["name"]) == ("bar", "bar")


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
async def test_import_skill_refuses_a_scope_another_skill_of_its_name_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _pin(ManifestEntry(name="sk", skill_id="other", version=1))
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=3))
    )

    with pytest.raises(
        _service.RegistrySkillsError, match="another shared skill named 'sk'"
    ):
        await _service.import_skill(build_test_vibe_config(), "cid", version=3)

    manifest = _manifest.load_sync(_manifest.global_manifest_path())
    assert [(e.name, e.skill_id) for e in manifest.skills] == [("sk", "other")]
    assert not await _store.is_materialized("cid", 3)


@pytest.mark.asyncio
@respx.mock
async def test_import_skill_reports_a_store_it_cannot_write_as_a_registry_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=3))
    )
    _store.store_root().parent.mkdir(parents=True, exist_ok=True)
    _store.store_root().write_text("not a directory", encoding="utf-8")

    with pytest.raises(_service.RegistrySkillsError, match="could not store skill cid"):
        await _service.import_skill(build_test_vibe_config(), "cid", version=3)

    assert _manifest.load_sync(_manifest.global_manifest_path()).skills == []


@pytest.mark.asyncio
@respx.mock
async def test_import_skill_says_why_when_the_version_lock_cannot_be_taken(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_store, "STORE_LOCK_WAIT", timedelta(0))
    respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=3))
    )
    lock = _store.skill_dir("cid", 3).parent / ".3.lock"

    with (
        held_by_another_process(lock),
        pytest.raises(_service.RegistrySkillsError) as raised,
    ):
        await _service.import_skill(build_test_vibe_config(), "cid", version=3)

    assert (
        str(raised.value)
        == "could not store skill cid: could not lock version 3 of cid"
    )


@pytest.mark.asyncio
@respx.mock
async def test_a_pin_renamed_upstream_keeps_answering_to_its_manifest_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = respx.get(f"{_URL}/cid")
    route.mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "foo", version=5))
    )
    await _service.import_skill(build_test_vibe_config(), "cid", version=5)
    route.mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "bar", version=5))
    )

    await _service.set_skill_version(build_test_vibe_config(), "foo", 5)

    config = build_test_vibe_config(experimental_enable_registry_skills=True)
    manifest = _manifest.load_sync(_manifest.global_manifest_path())
    skills = SkillManager(lambda: config).available_skills
    assert [entry.name for entry in manifest.skills] == ["bar"]
    assert list(skills) == ["bar"]
    assert _service.remove_skill("bar")


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
async def test_import_skill_naming_the_latest_alias_imports_as_naming_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route = respx.get(f"{_URL}/cid").mock(
        return_value=httpx.Response(200, json=_skill_payload("cid", "sk", version=4))
    )

    named = await _service.import_skill(build_test_vibe_config(), "cid", alias="latest")
    unnamed = await _service.import_skill(build_test_vibe_config(), "cid")

    assert named == unnamed
    assert route.calls[0].request.url == route.calls[1].request.url
    assert _resolved.get("cid", "latest") is None


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
