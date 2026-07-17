from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from vibe.core.skills.registry import _manifest
from vibe.core.skills.registry._manifest import ManifestEntry, SkillManifest


def test_upsert_replaces_by_name() -> None:
    m = SkillManifest()
    m.upsert(ManifestEntry(name="a", skill_id="x", version=1))
    m.upsert(ManifestEntry(name="a", skill_id="x", version=2))
    assert len(m.skills) == 1
    assert m.skills[0].version == 2


def test_remove() -> None:
    m = SkillManifest(skills=[ManifestEntry(name="a", skill_id="x", version=1)])
    assert m.remove("a") is True
    assert m.remove("missing") is False
    assert m.skills == []


def test_entry_alias_only_for_string_version() -> None:
    assert ManifestEntry(name="a", skill_id="x", version="latest").alias == "latest"
    assert ManifestEntry(name="a", skill_id="x", version=3).alias is None


@pytest.mark.asyncio
async def test_save_load_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "skills.toml"
    m = SkillManifest(
        skills=[ManifestEntry(name="grill-me", skill_id="uuid", version=3)]
    )
    await _manifest.save(path, m)

    loaded = await _manifest.load(path)
    assert loaded.skills == m.skills


@pytest.mark.asyncio
async def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "skills.toml"
    await _manifest.save(path, SkillManifest())
    assert path.is_file()


@pytest.mark.asyncio
async def test_load_missing_returns_empty(tmp_path: Path) -> None:
    assert (await _manifest.load(tmp_path / "nope.toml")).skills == []


@pytest.mark.asyncio
async def test_load_malformed_returns_empty(tmp_path: Path) -> None:
    path = tmp_path / "bad.toml"
    path.write_text("this is = = not valid toml [[[")
    assert (await _manifest.load(path)).skills == []


@pytest.mark.asyncio
async def test_project_manifest_paths_dedups_and_drops_global(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    global_root = tmp_path / "home"
    proj_a = tmp_path / "a"
    proj_b = tmp_path / "b"

    monkeypatch.setattr(
        _manifest, "global_manifest_path", lambda: global_root / ".vibe" / "skills.toml"
    )

    manager = SimpleNamespace(project_roots=[global_root, proj_a, proj_b, proj_a])
    monkeypatch.setattr(_manifest, "get_harness_files_manager", lambda: manager)

    assert await _manifest.project_manifest_paths() == [
        (proj_a / ".vibe" / "skills.toml").resolve(),
        (proj_b / ".vibe" / "skills.toml").resolve(),
    ]
