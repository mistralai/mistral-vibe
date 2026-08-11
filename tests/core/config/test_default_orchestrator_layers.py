from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
import tomli_w

from tests.conftest import get_base_config
from vibe.core.config import build_default_orchestrator
from vibe.core.config.harness_files import (
    init_harness_files_manager,
    reset_harness_files_manager,
)
from vibe.core.trusted_folders import trusted_folders_manager


def _write_user_config(config_dir: Path, extra: dict[str, object]) -> None:
    data = get_base_config()
    data.update(extra)
    with (config_dir / "config.toml").open("wb") as f:
        tomli_w.dump(data, f)


def _write_project_config(
    tmp_working_directory: Path, extra: dict[str, object]
) -> Path:
    project_vibe_dir = tmp_working_directory / ".vibe"
    project_vibe_dir.mkdir(parents=True, exist_ok=True)
    project_config = project_vibe_dir / "config.toml"
    with project_config.open("wb") as f:
        tomli_w.dump(extra, f)
    return project_config


def _project_vibe_dir(tmp_working_directory: Path) -> Path:
    return tmp_working_directory / ".vibe"


class TestBothTomlLayersInstalled:
    @pytest.mark.asyncio
    async def test_project_overrides_user_scalar(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        _write_user_config(config_dir, {"theme": "user-theme"})
        _write_project_config(tmp_working_directory, {"theme": "project-theme"})
        trusted_folders_manager.add_trusted(_project_vibe_dir(tmp_working_directory))

        orch = await build_default_orchestrator()

        assert orch.config.theme == "project-theme"
        assert orch.writable_layer_name == "project-toml"

    @pytest.mark.asyncio
    async def test_user_only_field_survives_when_project_absent(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        _write_user_config(config_dir, {"theme": "user-theme"})
        assert not _project_vibe_dir(tmp_working_directory).exists()

        orch = await build_default_orchestrator()

        assert orch.config.theme == "user-theme"
        assert orch.writable_layer_name == "user-toml"

    @pytest.mark.asyncio
    async def test_union_merge_combines_connectors_and_project_wins_on_clash(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        _write_user_config(
            config_dir, {"connectors": [{"name": "alpha", "disabled": False}]}
        )
        _write_project_config(
            tmp_working_directory,
            {"connectors": [{"name": "beta"}, {"name": "alpha", "disabled": True}]},
        )
        trusted_folders_manager.add_trusted(_project_vibe_dir(tmp_working_directory))

        orch = await build_default_orchestrator()

        by_name = {c.name: c for c in orch.config.connectors}
        assert set(by_name) == {"alpha", "beta"}
        assert by_name["alpha"].disabled is True

    @pytest.mark.asyncio
    async def test_concat_merge_concatenates_disabled_tools(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        _write_user_config(config_dir, {"disabled_tools": ["user-tool"]})
        _write_project_config(
            tmp_working_directory, {"disabled_tools": ["project-tool"]}
        )
        trusted_folders_manager.add_trusted(_project_vibe_dir(tmp_working_directory))

        orch = await build_default_orchestrator()

        assert orch.config.disabled_tools == ["user-tool", "project-tool"]

    @pytest.mark.asyncio
    async def test_untrusted_project_falls_back_to_user_only(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        _write_user_config(config_dir, {"theme": "user-theme"})
        _write_project_config(tmp_working_directory, {"theme": "project-theme"})
        # Deliberately do NOT trust the project .vibe dir. The untrusted project
        # layer loads empty (so effective config equals the user config) and the
        # write target falls back to the user layer.
        orch = await build_default_orchestrator()

        assert orch.config.theme == "user-theme"
        assert orch.writable_layer_name == "user-toml"

    @pytest.mark.asyncio
    async def test_no_project_file_write_target_is_user(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        _write_user_config(config_dir, {"theme": "user-theme"})
        assert not _project_vibe_dir(tmp_working_directory).exists()

        orch = await build_default_orchestrator()

        assert orch.config.theme == "user-theme"
        assert orch.writable_layer_name == "user-toml"

    @pytest.mark.asyncio
    async def test_project_only_no_file_creates_project_config_on_write(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        reset_harness_files_manager()
        init_harness_files_manager("project")
        try:
            project_config = _project_vibe_dir(tmp_working_directory) / "config.toml"
            assert not project_config.exists()

            orch = await build_default_orchestrator()

            assert orch.writable_layer_name == "project-toml"
            failures = await orch.set_field("/theme", "persisted-theme")
            assert failures == []
            assert project_config.is_file()
            with project_config.open("rb") as f:
                assert tomllib.load(f)["theme"] == "persisted-theme"
        finally:
            reset_harness_files_manager()
            init_harness_files_manager("user", "project")

    @pytest.mark.asyncio
    async def test_no_persistent_sources_writes_to_runtime_overrides(
        self, config_dir: Path
    ) -> None:
        reset_harness_files_manager()
        init_harness_files_manager()
        try:
            orch = await build_default_orchestrator(require_api_key=False)

            assert orch.writable_layer_name == "overrides"
            failures = await orch.set_field("/theme", "runtime-theme")

            assert failures == []
            assert orch.config.theme == "runtime-theme"
            with (config_dir / "config.toml").open("rb") as f:
                assert tomllib.load(f).get("theme") != "runtime-theme"
        finally:
            reset_harness_files_manager()
            init_harness_files_manager("user", "project")

    @pytest.mark.asyncio
    async def test_launched_from_home_does_not_double_user_config(
        self, config_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _write_user_config(config_dir, {"disabled_tools": ["user-tool"]})
        # cwd == user home, so cwd/.vibe is the user config dir itself.
        monkeypatch.chdir(config_dir.parent)

        orch = await build_default_orchestrator()

        assert orch.config.disabled_tools == ["user-tool"]
        layer_names = {layer.name for layer in orch.layers}
        assert "project-toml" not in layer_names
        assert orch.writable_layer_name == "user-toml"

    @pytest.mark.asyncio
    async def test_both_layers_present_in_stack(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        _write_user_config(config_dir, {"theme": "user-theme"})
        _write_project_config(tmp_working_directory, {"theme": "project-theme"})
        trusted_folders_manager.add_trusted(_project_vibe_dir(tmp_working_directory))

        orch = await build_default_orchestrator()

        layer_names = {layer.name for layer in orch.layers}
        assert {"user-toml", "project-toml"}.issubset(layer_names)
        # Order: user before project.
        ordered = [layer.name for layer in orch.layers]
        assert ordered.index("user-toml") < ordered.index("project-toml")
