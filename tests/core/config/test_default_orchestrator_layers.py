from __future__ import annotations

from pathlib import Path
import tomllib

import pytest
import tomli_w

from tests.conftest import get_base_config
from vibe.core.agents.manager import AgentManager
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


class TestSessionPromptResolution:
    @pytest.mark.asyncio
    async def test_project_prompt_wins_for_a_session_started_elsewhere(
        self, config_dir: Path, tmp_path: Path
    ) -> None:
        """Prompt validation and loading use the session workdir, not launch cwd."""
        project_prompts = tmp_path / ".vibe" / "prompts"
        project_prompts.mkdir(parents=True)
        (project_prompts / "shared.md").write_text("Project prompt", encoding="utf-8")

        user_prompts = config_dir / "prompts"
        user_prompts.mkdir()
        (user_prompts / "shared.md").write_text("User prompt", encoding="utf-8")

        trusted_folders_manager.trust_for_session(tmp_path)
        harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
            tmp_path
        )

        orchestrator = await build_default_orchestrator(
            {"system_prompt_id": "shared"}, harness_files=harness_files
        )

        assert orchestrator.config.system_prompt == "Project prompt"

    @pytest.mark.asyncio
    async def test_custom_primary_agent_validates_its_project_prompt(
        self, tmp_path: Path
    ) -> None:
        """Copied profile validation keeps the session prompt search context."""
        project_vibe = tmp_path / ".vibe"
        (project_vibe / "prompts").mkdir(parents=True)
        (project_vibe / "prompts" / "agent.md").write_text(
            "Project agent prompt", encoding="utf-8"
        )
        (project_vibe / "agents").mkdir()
        (project_vibe / "agents" / "project-agent.toml").write_text(
            'description = "Project agent"\n'
            'agent_type = "agent"\n'
            'system_prompt_id = "agent"\n',
            encoding="utf-8",
        )

        trusted_folders_manager.trust_for_session(tmp_path)
        harness_files = HarnessFilesManager(sources=("user", "project")).for_session(
            tmp_path
        )
        orchestrator = await build_default_orchestrator(harness_files=harness_files)

        agents = AgentManager(
            orchestrator, initial_agent="project-agent", harness_files=harness_files
        )

        assert agents.active_profile.name == "project-agent"
        assert orchestrator.config.system_prompt == "Project agent prompt"
