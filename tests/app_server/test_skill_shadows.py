from __future__ import annotations

from pathlib import Path
import sys

import pytest

from tests.conftest import build_test_vibe_config
from tests.skills.conftest import create_skill
from tests.skills.registry.conftest import turn_registry_skills_on
from tests.stubs.local_sandbox import build_trusted_project
from vibe.app_server._skill_shadows import ShadowWarnings
from vibe.app_server.local import LocalHarnessHost, LocalHarnessOptions
from vibe.app_server.protocol import SessionOptions
from vibe.core.agents.models import BuiltinAgentName
from vibe.core.config.harness_files._paths import GLOBAL_SKILLS_DIR
from vibe.core.skills.manager import SkillManager
from vibe.core.trusted_folders import trusted_folders_manager

pytestmark = [
    pytest.mark.timeout(60),
    pytest.mark.skipif(
        sys.platform == "win32", reason="shares the POSIX sandbox test fixtures"
    ),
]


def _write_skill(root: Path, name: str, description: str) -> None:
    skill = root / name
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nBody.\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_two_sessions_of_one_process_warn_once_about_a_shadowed_skill(
    tmp_path: Path,
    config_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    turn_registry_skills_on(config_dir)
    project = build_trusted_project(tmp_path / "project", monkeypatch)
    _write_skill(GLOBAL_SKILLS_DIR.path, "deploy", "Deploy anywhere.")
    _write_skill(project / ".vibe" / "skills", "deploy", "Deploy this project.")
    options = LocalHarnessOptions(
        session_options=SessionOptions(
            cwd=str(project),
            agent=BuiltinAgentName.AUTO_APPROVE,
            trust_workspace=True,
            headless=True,
        )
    )
    host = LocalHarnessHost()

    try:
        with caplog.at_level("WARNING", logger="vibe"):
            for _ in range(2):
                session = await host.start(options)
                await session.close()
    finally:
        await host.close()

    warned = [r.getMessage() for r in caplog.records if "shadowed" in r.getMessage()]
    assert len(warned) == 1
    assert "Skill 'deploy' from global" in warned[0]


def test_a_shadowed_skill_is_a_warning_once_per_process(
    tmp_working_directory: Path, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    trusted_folders_manager.add_trusted(tmp_working_directory)
    skills_dir = tmp_path / "skills"
    create_skill(skills_dir, "deploy", "configured path")
    create_skill(tmp_working_directory / ".vibe" / "skills", "deploy", "project file")
    config = build_test_vibe_config(
        skill_paths=[skills_dir], experimental_enable_registry_skills=True
    )
    warnings = ShadowWarnings()

    with caplog.at_level("WARNING", logger="vibe"):
        managers = [SkillManager(lambda: config) for _ in range(2)]
        assert not [r for r in caplog.records if "shadowed" in r.getMessage()]
        for manager in managers:
            warnings.warn(manager.shadowed)

    (record,) = [r for r in caplog.records if "shadowed" in r.getMessage()]
    assert str(skills_dir / "deploy" / "SKILL.md") in record.getMessage()
