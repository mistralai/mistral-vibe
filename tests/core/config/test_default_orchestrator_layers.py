from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
import tomli_w

from tests.conftest import get_base_config
from vibe.core.config import build_default_orchestrator
from vibe.core.config.harness_files import (
    HarnessFilesManager,
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
        # Project still wins the merge, but writes default to the user config.
        assert orch.writable_layer_name == "user-toml"

    @pytest.mark.asyncio
    async def test_discovered_project_config_does_not_capture_writes(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        _write_user_config(config_dir, {"theme": "user-theme"})
        project_config = _write_project_config(
            tmp_working_directory, {"theme": "project-theme"}
        )
        trusted_folders_manager.add_trusted(_project_vibe_dir(tmp_working_directory))

        orch = await build_default_orchestrator()
        assert await orch.set_field("/enable_connectors", False) == []

        with project_config.open("rb") as f:
            assert "enable_connectors" not in tomllib.load(f)
        with (config_dir / "config.toml").open("rb") as f:
            assert tomllib.load(f)["enable_connectors"] is False

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
            orch = await build_default_orchestrator()

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


class TestSuppliedUserConfig:
    @pytest.mark.asyncio
    async def test_supplied_config_stands_in_for_the_file_and_saves_in_memory(
        self, config_dir: Path
    ) -> None:
        """*Prepare*: A user config file, and harness files that supply another
        user config.
        *Do*: Build the config from the supplied one and save a field.
        *Assert*: The supplied config is read, the save lands in memory, and the
        file is untouched.
        """
        # Prepare
        _write_user_config(config_dir, {"theme": "file-theme"})
        before = (config_dir / "config.toml").read_bytes()
        supplied = {**get_base_config(), "theme": "supplied-theme"}
        harness_files = HarnessFilesManager(sources=("user",), user_config=supplied)

        # Do
        orch = await build_default_orchestrator(harness_files=harness_files)
        await orch.set_field("/enable_connectors", False)

        # Assert
        assert orch.config.theme == "supplied-theme"
        assert orch.writable_layer_name == "user-supplied"
        assert orch.config.enable_connectors is False
        assert (config_dir / "config.toml").read_bytes() == before
        assert "enable_connectors" not in supplied

    @pytest.mark.asyncio
    async def test_supplied_config_loads_and_migrates_as_the_file_would(self) -> None:
        """*Prepare*: A supplied config in the file's shape: models as a list,
        and a tool under its old name.
        *Do*: Build the config from it.
        *Assert*: The model resolves and the tool carries its new name, as they
        would from the file. The caller's document is unchanged.
        """
        # Prepare
        supplied = {**get_base_config(), "disabled_tools": ["read"]}
        harness_files = HarnessFilesManager(sources=("user",), user_config=supplied)

        # Do
        orch = await build_default_orchestrator(harness_files=harness_files)

        # Assert
        assert orch.config.get_active_model().name == "mistral-vibe-cli-latest"
        assert orch.config.disabled_tools == ["read_file"]
        assert supplied["disabled_tools"] == ["read"]

    def test_a_session_keeps_the_supplied_config(self, tmp_path: Path) -> None:
        """*Prepare*: Harness files that supply a user config.
        *Do*: Derive the files of a session.
        *Assert*: It keeps the supplied config.
        """
        # Prepare
        supplied = {"theme": "supplied-theme"}
        harness_files = HarnessFilesManager(sources=("user",), user_config=supplied)

        # Do
        session = harness_files.for_session(tmp_path)

        # Assert
        assert session.user_config is supplied


class TestSeededShellTimeout:
    @pytest.mark.asyncio
    async def test_a_seeded_bash_default_timeout_is_dropped_so_the_config_loads(
        self, config_dir: Path
    ) -> None:
        """*Prepare*: A user config.toml that Vibe's first run wrote before July 2026,
        with `[tools.bash] default_timeout = 300` and no maximum.
        *Do*: Build the config from it.
        *Assert*: It loads, and the seeded default is gone from the file, so bash keeps
        Vibe's own default and maximum. The bash allowlist next to it stays.
        """
        # Prepare
        _write_user_config(
            config_dir,
            {"tools": {"bash": {"default_timeout": 300, "allowlist": ["echo"]}}},
        )

        # Do
        orch = await build_default_orchestrator()

        # Assert
        assert "default_timeout" not in orch.config.tools["bash"]
        with (config_dir / "config.toml").open("rb") as f:
            persisted_bash = tomllib.load(f)["tools"]["bash"]
        assert "default_timeout" not in persisted_bash
        assert "echo" in persisted_bash["allowlist"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("bash", "kept"),
        [
            ({"default_timeout": 300, "max_timeout_seconds": 600}, (300, 600)),
            ({"default_timeout": 60, "max_timeout_seconds": 300}, (60, 300)),
        ],
    )
    async def test_shell_timeouts_set_together_are_kept(
        self, config_dir: Path, bash: dict[str, int], kept: tuple[int, int]
    ) -> None:
        """*Prepare*: A user config.toml whose bash default and maximum are both set.
        *Do*: Build the config from it.
        *Assert*: Both are kept as set.
        """
        # Prepare
        _write_user_config(config_dir, {"tools": {"bash": bash}})

        # Do
        orch = await build_default_orchestrator()

        # Assert
        tool = orch.config.tools["bash"]
        assert (tool["default_timeout"], tool["max_timeout_seconds"]) == kept

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("user_bash", "project_bash"),
        [
            ({"max_timeout_seconds": 600}, {"default_timeout": 300}),
            ({"default_timeout": 300}, {"max_timeout_seconds": 600}),
        ],
        ids=["project-default", "user-default"],
    )
    async def test_a_300_default_paired_with_another_files_maximum_is_kept(
        self,
        config_dir: Path,
        tmp_working_directory: Path,
        user_bash: dict[str, int],
        project_bash: dict[str, int],
    ) -> None:
        """*Prepare*: A bash `default_timeout = 300` in one config file and
        `max_timeout_seconds = 600` in the other (user and project).
        *Do*: Build the config from both.
        *Assert*: It loads as 300 / 600; the 300 is not taken for the seeded value,
        because the merged config sets both.
        """
        # Prepare
        _write_user_config(config_dir, {"tools": {"bash": user_bash}})
        _write_project_config(tmp_working_directory, {"tools": {"bash": project_bash}})
        trusted_folders_manager.add_trusted(_project_vibe_dir(tmp_working_directory))

        # Do
        orch = await build_default_orchestrator()

        # Assert
        tool = orch.config.tools["bash"]
        assert (tool["default_timeout"], tool["max_timeout_seconds"]) == (300, 600)

    @pytest.mark.asyncio
    async def test_a_project_bash_default_timeout_of_300_alone_still_fails(
        self, config_dir: Path, tmp_working_directory: Path
    ) -> None:
        """*Prepare*: A trusted project config with `[tools.bash] default_timeout = 300`
        alone. Vibe's first run seeded only the user config.toml.
        *Do*: Build the config.
        *Assert*: It is a config error naming both settings, and the project file
        keeps its value.
        """
        # Prepare
        _write_user_config(config_dir, {})
        project_config = _write_project_config(
            tmp_working_directory, {"tools": {"bash": {"default_timeout": 300}}}
        )
        trusted_folders_manager.add_trusted(_project_vibe_dir(tmp_working_directory))

        # Do / Assert
        with pytest.raises(
            Exception,
            match="tools.bash: default_timeout is set without max_timeout_seconds",
        ):
            await build_default_orchestrator()
        with project_config.open("rb") as f:
            assert tomllib.load(f)["tools"]["bash"] == {"default_timeout": 300}

    @pytest.mark.asyncio
    async def test_a_bash_default_timeout_other_than_the_seeded_one_alone_still_fails(
        self, config_dir: Path
    ) -> None:
        """*Prepare*: A user config.toml with `[tools.bash] default_timeout = 600` alone.
        *Do*: Build the config from it.
        *Assert*: It is a config error naming both settings; the migration leaves a
        value Vibe never wrote alone.
        """
        # Prepare
        _write_user_config(config_dir, {"tools": {"bash": {"default_timeout": 600}}})

        # Do / Assert
        with pytest.raises(
            Exception,
            match="tools.bash: default_timeout is set without max_timeout_seconds",
        ):
            await build_default_orchestrator()
