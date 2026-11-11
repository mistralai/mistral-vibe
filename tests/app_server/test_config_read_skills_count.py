from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest

from tests.stubs.local_sandbox import RecordingSandbox
from vibe.app_server._sandbox_skills import SandboxSessionSkills, SandboxSkills
from vibe.app_server._unified_harness_backend_adapter import _config_read_response
from vibe.core.config import VibeConfigSchema
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.skills.models import SkillInfo, SkillScope


def _write_skill(root: Path, name: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: The {name} skill\n---\n\n# {name}\n",
        encoding="utf-8",
    )


@pytest.fixture
def user_skills(config_dir: Path) -> None:
    for name in ("deploy", "lint"):
        _write_skill(config_dir / "skills", name)


@pytest.mark.asyncio
@pytest.mark.usefixtures("user_skills")
async def test_a_session_config_read_counts_the_skills_it_loads(
    make_orchestrator: Callable[[], Awaitable[ConfigOrchestrator[VibeConfigSchema]]],
) -> None:
    orchestrator = await make_orchestrator()

    answer = await _config_read_response(
        orchestrator, HarnessFilesManager(sources=("user",))
    )

    assert answer.skills_count == 2


@pytest.mark.asyncio
@pytest.mark.usefixtures("user_skills")
async def test_a_sandboxed_session_config_read_counts_its_project_skills(
    make_orchestrator: Callable[[], Awaitable[ConfigOrchestrator[VibeConfigSchema]]],
) -> None:
    orchestrator = await make_orchestrator()
    release = SkillInfo(
        name="release",
        description="Ships it.",
        prompt="# release",
        skill_path=Path("/sandbox/project/.vibe/skills/release/SKILL.md"),
        scope=SkillScope.PROJECT,
    )

    sandbox = SandboxSkills(RecordingSandbox(Path("/unused")))

    answer = await _config_read_response(
        orchestrator,
        HarnessFilesManager(sources=("user",)),
        sandbox=SandboxSessionSkills(sandbox, {"release": release}, ()),
    )

    assert answer.skills_count == 3
