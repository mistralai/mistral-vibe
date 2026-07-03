from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from vibe.core.config import build_default_orchestrator
from vibe.core.trusted_folders import trusted_folders_manager


@pytest.mark.asyncio
async def test_build_default_orchestrator_uses_standard_layer_priority(
    config_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_working_directory: Path
) -> None:
    user_config_path = config_dir / "config.toml"
    user_config_path.write_text(
        """\
default_agent = "default"
context_warnings = false
ask_confirmation_on_exit = true
displayed_workdir = "user-only"
""",
        encoding="utf-8",
    )
    project_config_path = tmp_working_directory / ".vibe" / "config.toml"
    project_config_path.parent.mkdir(parents=True)
    project_config_path.write_text(
        """\
default_agent = "plan"
context_warnings = true
ask_confirmation_on_exit = true
""",
        encoding="utf-8",
    )
    trusted_folders_manager.add_trusted(project_config_path.parent)
    monkeypatch.setenv("VIBE_DEFAULT_AGENT", "accept-edits")
    monkeypatch.setenv("VIBE_ASK_CONFIRMATION_ON_EXIT", "false")

    orchestrator = await build_default_orchestrator({"default_agent": "auto-approve"})

    assert orchestrator.config.default_agent == "auto-approve"
    assert orchestrator.config.context_warnings is True
    assert orchestrator.config.ask_confirmation_on_exit is False
    assert orchestrator.config.displayed_workdir == ""

    result = await orchestrator.set_field("/displayed_workdir", "patched")

    assert result == []
    with project_config_path.open("rb") as file:
        assert tomllib.load(file)["displayed_workdir"] == "patched"
    with user_config_path.open("rb") as file:
        assert tomllib.load(file)["displayed_workdir"] == "user-only"


@pytest.mark.asyncio
async def test_build_default_orchestrator_migrates_user_config(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    config_path.write_text(
        """\
active_model = "devstral-2"

[[models]]
name = "mistral-vibe-cli-latest"
alias = "devstral-2"
provider = "mistral"
""",
        encoding="utf-8",
    )

    orchestrator = await build_default_orchestrator()

    assert orchestrator.config.active_model == "mistral-medium-3.5"
    with config_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["active_model"] == "mistral-medium-3.5"
    assert persisted["models"][0]["alias"] == "mistral-medium-3.5"


@pytest.mark.asyncio
async def test_build_default_orchestrator_migrates_project_config_when_present(
    tmp_working_directory: Path,
) -> None:
    config_path = tmp_working_directory / ".vibe" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        """\
active_model = "devstral-2"

[[models]]
name = "mistral-vibe-cli-latest"
alias = "devstral-2"
provider = "mistral"
""",
        encoding="utf-8",
    )
    trusted_folders_manager.add_trusted(config_path.parent)

    orchestrator = await build_default_orchestrator()

    assert orchestrator.config.active_model == "mistral-medium-3.5"
    with config_path.open("rb") as file:
        persisted = tomllib.load(file)
    assert persisted["active_model"] == "mistral-medium-3.5"
    assert persisted["models"][0]["alias"] == "mistral-medium-3.5"
