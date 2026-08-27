"""Skill catalogue expectations both backends have to meet.

The CLI resolves ``/skill-name`` out of the runtime snapshot and the settings
screen counts custom skills from it, so a backend that reports no skills is one
where those features silently do nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.app_server.backend_contract.conftest import BackendContractConnection
from vibe.app_server._model import validate_wire
from vibe.app_server.protocol import (
    SessionOptions,
    SkillsListParams,
    SkillsListResponse,
)
from vibe.app_server.session import AppServerSession

_PROMPT = "Read the diff twice before commenting."


def _write_skill(cwd: Path, name: str, description: str, prompt: str) -> None:
    skill = cwd / ".vibe" / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n{prompt}\n",
        encoding="utf-8",
    )


@pytest.fixture
def backend_contract_session_options(tmp_path: Path) -> SessionOptions:
    _write_skill(tmp_path, "code-review", "Review a diff for defects.", _PROMPT)
    return SessionOptions(cwd=str(tmp_path), trust_workspace=True)


@pytest.mark.asyncio
async def test_a_workspace_skill_reaches_the_runtime_snapshot(
    backend_contract_session: AppServerSession,
) -> None:
    """Prepare a workspace ``SKILL.md``.

    Do open a session.

    Assert the client can resolve it by name. This is the lookup ``/skill-name``
    goes through, so an empty catalogue is a dead slash command.
    """
    skill = backend_contract_session.resources.runtime.get_skill("code-review")

    assert skill is not None
    assert skill.description == "Review a diff for defects."
    assert _PROMPT in skill.prompt
    assert skill.source == "local"
    assert skill.user_invocable is True


@pytest.mark.asyncio
async def test_builtin_skills_are_reported_as_builtin(
    backend_contract_session: AppServerSession,
) -> None:
    """Prepare a session.

    Do look up a skill that ships with Vibe.

    Assert it is not counted as custom. The Unified backend writes builtins to
    disk so Core has a real path, and a file on disk must not make them look
    like something the user installed.
    """
    builtin = backend_contract_session.resources.runtime.get_skill("vibe")

    assert builtin is not None
    assert builtin.source == "builtin"
    assert backend_contract_session.resources.runtime.custom_skills_count == 1


@pytest.mark.asyncio
async def test_skills_list_answers_with_the_same_catalogue(
    backend_contract_connection: BackendContractConnection,
    backend_contract_session: AppServerSession,
) -> None:
    """Prepare a session.

    Do call ``skills/list``.

    Assert it agrees with the snapshot the session was opened with. Two sources
    of truth for one catalogue is how a client ends up offering a skill the
    backend will not load.
    """
    response = validate_wire(
        SkillsListResponse,
        await backend_contract_connection.client.request(
            "skills/list",
            SkillsListParams(session_id=backend_contract_session.state.session.id),
        ),
    )

    listed = {skill.name for skill in response.skills}
    assert "code-review" in listed
    assert listed == {
        skill.name for skill in backend_contract_session.resources.runtime.skills
    }


@pytest.mark.asyncio
async def test_reload_picks_up_a_skill_written_after_the_session_opened(
    backend_contract_session: AppServerSession, tmp_path: Path
) -> None:
    """Prepare a session, then write a second ``SKILL.md`` into the workspace.

    Do reload the configuration.

    Assert the new skill is in the catalogue. Authoring a skill and reloading is
    the whole edit loop; a backend that only scans at bind time makes the user
    restart to see their own file.
    """
    _write_skill(tmp_path, "release-notes", "Draft release notes.", "Group by theme.")

    await backend_contract_session.resources.config.reload()

    skill = backend_contract_session.resources.runtime.get_skill("release-notes")
    assert skill is not None
    assert skill.description == "Draft release notes."
