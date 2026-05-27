from __future__ import annotations

from pathlib import Path

import pytest

from vibe.core.config.layer import UntrustedLayerError
from vibe.core.config.layers.project import ProjectConfigLayer
from vibe.core.paths._vibe_home import GlobalPath
from vibe.core.trusted_folders import trusted_folders_manager


@pytest.mark.asyncio
async def test_reads_toml_when_trusted(tmp_working_directory: Path) -> None:
    trusted_folders_manager.add_trusted(tmp_working_directory)
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('active_model = "project-model"\n')

    layer = ProjectConfigLayer(path=tmp_working_directory)
    data = await layer.load()
    assert data.model_extra == {"active_model": "project-model"}


@pytest.mark.asyncio
async def test_untrusted_raises(tmp_working_directory: Path) -> None:
    trusted_folders_manager.add_untrusted(tmp_working_directory)
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('active_model = "project-model"\n')

    layer = ProjectConfigLayer(path=tmp_working_directory)
    with pytest.raises(UntrustedLayerError):
        await layer.load()


@pytest.mark.asyncio
async def test_unresolved_trust_defaults_to_untrusted(
    tmp_working_directory: Path,
) -> None:
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('key = "value"\n')

    layer = ProjectConfigLayer(path=tmp_working_directory)
    with pytest.raises(UntrustedLayerError):
        await layer.load()


@pytest.mark.asyncio
async def test_missing_file_returns_empty(tmp_working_directory: Path) -> None:
    trusted_folders_manager.add_trusted(tmp_working_directory)

    layer = ProjectConfigLayer(path=tmp_working_directory)
    data = await layer.load()
    assert data.model_extra == {}


@pytest.mark.asyncio
async def test_default_name(tmp_working_directory: Path) -> None:
    layer = ProjectConfigLayer(path=tmp_working_directory)
    assert layer.name == "project-toml"


@pytest.mark.asyncio
async def test_apply_raises_not_implemented(tmp_working_directory: Path) -> None:
    layer = ProjectConfigLayer(path=tmp_working_directory)
    with pytest.raises(NotImplementedError, match="M2"):
        await layer.apply({"op": "set"})


@pytest.mark.asyncio
async def test_trust_uses_path_parent_for_resolution(
    tmp_working_directory: Path,
) -> None:
    project_dir = tmp_working_directory / "my-project"
    project_dir.mkdir()

    config_path = project_dir / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('value = "trusted"\n')
    trusted_folders_manager.add_trusted(project_dir / ".vibe")

    layer = ProjectConfigLayer(path=project_dir)
    data = await layer.load()
    assert data.model_extra == {"value": "trusted"}


@pytest.mark.asyncio
async def test_grant_trust_marks_folder_trusted(tmp_working_directory: Path) -> None:
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('key = "value"\n')
    layer = ProjectConfigLayer(path=tmp_working_directory)
    trusted_folders_manager.add_untrusted(tmp_working_directory / ".vibe")

    await layer.resolve_trust()
    await layer.grant_trust()
    assert trusted_folders_manager.is_trusted(tmp_working_directory / ".vibe") is True


@pytest.mark.asyncio
async def test_revoke_trust_marks_folder_untrusted(tmp_working_directory: Path) -> None:
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('key = "value"\n')
    layer = ProjectConfigLayer(path=tmp_working_directory)
    trusted_folders_manager.add_trusted(tmp_working_directory / ".vibe")

    await layer.resolve_trust()
    await layer.revoke_trust()
    assert trusted_folders_manager.is_trusted(tmp_working_directory / ".vibe") is False


@pytest.mark.asyncio
async def test_grant_trust_without_config_file_is_noop(
    tmp_working_directory: Path,
) -> None:
    layer = ProjectConfigLayer(path=tmp_working_directory)
    trusted_folders_manager.add_untrusted(tmp_working_directory)

    await layer.grant_trust()
    assert trusted_folders_manager.is_trusted(tmp_working_directory) is False


@pytest.mark.asyncio
async def test_revoke_trust_without_config_file_is_noop(
    tmp_working_directory: Path,
) -> None:
    layer = ProjectConfigLayer(path=tmp_working_directory)
    trusted_folders_manager.add_trusted(tmp_working_directory)

    await layer.revoke_trust()
    assert trusted_folders_manager.is_trusted(tmp_working_directory) is True


@pytest.mark.asyncio
async def test_trust_stored_at_vibe_subdir(tmp_working_directory: Path) -> None:
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('key = "value"\n')
    layer = ProjectConfigLayer(path=tmp_working_directory)

    await layer.resolve_trust()
    await layer.grant_trust()

    assert (
        str((tmp_working_directory / ".vibe").resolve())
        in trusted_folders_manager._trusted
    )
    assert str(tmp_working_directory.resolve()) not in trusted_folders_manager._trusted


@pytest.mark.asyncio
async def test_find_file_prefers_closest_config(tmp_working_directory: Path) -> None:
    parent_config = tmp_working_directory / ".vibe" / "config.toml"
    parent_config.parent.mkdir(parents=True, exist_ok=True)
    parent_config.write_text('level = "parent"\n')

    child = tmp_working_directory / "child"
    child.mkdir()
    child_config = child / ".vibe" / "config.toml"
    child_config.parent.mkdir(parents=True, exist_ok=True)
    child_config.write_text('level = "child"\n')

    trusted_folders_manager.add_trusted(child)
    layer = ProjectConfigLayer(path=child)
    data = await layer.load()
    assert data.model_extra == {"level": "child"}


@pytest.mark.asyncio
async def test_find_file_result_is_cached(tmp_working_directory: Path) -> None:
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("x = 1\n")
    trusted_folders_manager.add_trusted(tmp_working_directory)

    layer = ProjectConfigLayer(path=tmp_working_directory)
    await layer.load()

    assert layer._is_set is True
    cached_path = layer._config_file_path
    await layer.resolve_trust()
    assert layer._config_file_path is cached_path


@pytest.mark.asyncio
async def test_config_file_added_after_first_search_is_not_detected(
    tmp_working_directory: Path,
) -> None:
    layer = ProjectConfigLayer(path=tmp_working_directory)
    await layer._find_config_file()

    assert layer._is_set is True
    assert layer._config_file_path is None

    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('active_model = "new-model"\n')

    await layer._find_config_file()

    assert layer._config_file_path is None


@pytest.mark.asyncio
async def test_finds_config_in_parent_directory(tmp_working_directory: Path) -> None:
    trusted_folders_manager.add_trusted(tmp_working_directory)
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('active_model = "parent-model"\n')

    subdir = tmp_working_directory / "sub" / "project"
    subdir.mkdir(parents=True)

    layer = ProjectConfigLayer(path=subdir)
    data = await layer.load()
    assert data.model_extra == {"active_model": "parent-model"}


@pytest.mark.asyncio
async def test_trusted_ancestor_satisfies_trust_check(
    tmp_working_directory: Path,
) -> None:
    parent = tmp_working_directory / "workspace"
    parent.mkdir()
    child = parent / "project"
    child.mkdir()

    trusted_folders_manager.add_trusted(parent)

    config_path = child / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('active_model = "child-model"\n')

    layer = ProjectConfigLayer(path=child)
    data = await layer.load()
    assert data.model_extra == {"active_model": "child-model"}


@pytest.mark.asyncio
async def test_grant_trust_then_load_reads_config_without_prior_resolve(
    tmp_working_directory: Path,
) -> None:
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('key = "value"\n')

    layer = ProjectConfigLayer(path=tmp_working_directory)
    await layer.resolve_trust()
    await layer.grant_trust()
    data = await layer.load()

    assert data.model_extra == {"key": "value"}


@pytest.mark.asyncio
async def test_walk_stops_at_vibe_home_parent(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Create an isolated tmp root and point VIBE_HOME into it
    tmp_root = tmp_path_factory.mktemp("walk_stop")
    fake_vibe_home = tmp_root / ".vibe"
    fake_vibe_home.mkdir()
    monkeypatch.setattr(
        "vibe.core.config.layers.project.VIBE_HOME", GlobalPath(lambda: fake_vibe_home)
    )

    # Place a config at tmp_root/.vibe/config.toml — should NOT be picked up
    home_config = fake_vibe_home / "config.toml"
    home_config.write_text('active_model = "home-model"\n')

    # subdir lives inside tmp_root so the walk would reach the config without the stop guard
    subdir = tmp_root / "vibe-test-project"
    subdir.mkdir()
    trusted_folders_manager.add_trusted(subdir)

    layer = ProjectConfigLayer(path=subdir)
    data = await layer.load()
    assert data.model_extra == {}
