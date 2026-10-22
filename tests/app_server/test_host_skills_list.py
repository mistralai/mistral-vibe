"""``skills/list`` without a session answers for a session not opened yet.

Desktop's new-session composer offers ``/skill-name`` before any session
exists, so the passive Host has to report what a session at ``cwd`` would list.
"""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from vibe.app_server._connection_protocol import ClientInfo
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._host import HostRequestHandler
from vibe.app_server.protocol import SkillsListParams, SkillsListResponse
from vibe.core.config.harness_files import HarnessFilesManager


def _write_skill(root: Path, name: str, description: str) -> None:
    skill = root / ".vibe" / "skills" / name / "SKILL.md"
    skill.parent.mkdir(parents=True, exist_ok=True)
    skill.write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\nDo the thing.\n",
        encoding="utf-8",
    )


_CLIENT = ClientInfo(name="test-client", version="0.0.0")


async def _list(handler: HostRequestHandler, params: SkillsListParams) -> list[str]:
    result = await handler.dispatch(
        "skills/list",
        params.model_dump(mode="json", by_alias=True),
        client_info=_CLIENT,
    )
    return [skill.name for skill in cast(SkillsListResponse, result.response).skills]


@pytest.mark.asyncio
async def test_lists_a_trusted_projects_skills_without_a_session(
    tmp_path: Path,
) -> None:
    """Prepare a project skill.

    Do list skills for that project with workspace trust.

    Assert the project skill is offered.
    """
    _write_skill(tmp_path, "code-review", "Review a diff.")
    handler = HostRequestHandler(
        HarnessFilesManager(sources=("user", "project")), builtin_plugin_roots=[]
    )

    names = await _list(
        handler, SkillsListParams(cwd=str(tmp_path), trust_workspace=True)
    )

    assert "code-review" in names


@pytest.mark.asyncio
async def test_does_not_list_an_untrusted_projects_skills(tmp_path: Path) -> None:
    """Prepare a project skill in a folder nobody trusted.

    Do list skills for it without asking for trust.

    Assert the project skill is withheld, as a session there would withhold it.
    """
    _write_skill(tmp_path, "code-review", "Review a diff.")
    harness_files = HarnessFilesManager(sources=("user", "project"))
    handler = HostRequestHandler(harness_files, builtin_plugin_roots=[])

    names = await _list(handler, SkillsListParams(cwd=str(tmp_path)))

    assert "code-review" not in names


@pytest.mark.asyncio
async def test_trust_for_a_listing_does_not_outlive_it(tmp_path: Path) -> None:
    """Prepare a project skill and list it with workspace trust.

    Do list again without trust.

    Assert the second listing withholds it: the grant was scoped to one read.
    """
    _write_skill(tmp_path, "code-review", "Review a diff.")
    harness_files = HarnessFilesManager(sources=("user", "project"))
    handler = HostRequestHandler(harness_files, builtin_plugin_roots=[])

    await _list(handler, SkillsListParams(cwd=str(tmp_path), trust_workspace=True))
    names = await _list(handler, SkillsListParams(cwd=str(tmp_path)))

    assert "code-review" not in names
    assert harness_files.trust_store.is_trusted(tmp_path) is not True


@pytest.mark.asyncio
async def test_a_named_session_is_not_the_hosts_to_answer() -> None:
    handler = HostRequestHandler(HarnessFilesManager(sources=("user",)))

    with pytest.raises(RequestFailure):
        await handler.dispatch(
            "skills/list",
            SkillsListParams(session_id="missing").model_dump(
                mode="json", by_alias=True
            ),
        )
