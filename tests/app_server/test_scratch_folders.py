from __future__ import annotations

from pathlib import Path

import pytest

from vibe.app_server._scratch_folders import create_scratch_folder, scratch_folder_for
from vibe.app_server._worktree_session import session_harness_files
from vibe.app_server.protocol import (
    AgentConfig,
    SessionNoProject,
    SessionProjectStamp,
    SessionStartParams,
)
from vibe.core.config.harness_files import HarnessFilesManager
from vibe.core.paths import SCRATCH_DIR
from vibe.core.trusted_folders import maybe_build_workspace_trust_prompt

_PROJECTLESS = SessionStartParams(project=SessionNoProject())


@pytest.mark.asyncio
async def test_each_projectless_start_runs_in_a_new_scratch_folder() -> None:
    async with scratch_folder_for(_PROJECTLESS) as first:
        pass
    async with scratch_folder_for(_PROJECTLESS) as second:
        pass

    assert first.cwd is not None
    assert second.cwd is not None
    assert first.cwd != second.cwd
    for started in (first, second):
        folder = Path(started.cwd or "")
        assert folder.is_dir()
        assert folder.parent == SCRATCH_DIR.path.resolve()


@pytest.mark.asyncio
async def test_a_failed_start_removes_its_scratch_folder() -> None:
    folder: Path | None = None

    with pytest.raises(RuntimeError):
        async with scratch_folder_for(_PROJECTLESS) as started:
            folder = Path(started.cwd or "")
            raise RuntimeError("start failed")

    assert folder is not None
    assert not folder.exists()


@pytest.mark.asyncio
async def test_a_projectless_start_keeps_the_folder_it_names(tmp_path: Path) -> None:
    named = _PROJECTLESS.model_copy(
        update={"agent_config": AgentConfig(cwd=str(tmp_path))}
    )

    async with scratch_folder_for(named) as started:
        pass

    assert started is named
    assert not SCRATCH_DIR.path.exists()


@pytest.mark.asyncio
async def test_a_start_with_a_project_keeps_its_folder() -> None:
    params = SessionStartParams(
        agent_config=AgentConfig(cwd="/workspace"),
        project=SessionProjectStamp(kind="local", id="local-1"),
    )

    async with scratch_folder_for(params) as started:
        pass

    assert started is params
    assert not SCRATCH_DIR.path.exists()


def test_a_scratch_folder_kept_beside_a_project_is_writable_but_not_read(
    tmp_path: Path,
) -> None:
    scratch = create_scratch_folder()
    (scratch / "AGENTS.md").write_text("injected")
    project = tmp_path / "repo"
    project.mkdir()

    files = session_harness_files(HarnessFilesManager(), project, [project, scratch])

    assert scratch not in files.project_roots
    assert files.writable_roots == (scratch,)


def test_a_scratch_folder_offers_no_trust_prompt() -> None:
    scratch = create_scratch_folder()
    (scratch / "AGENTS.md").write_text("written by the agent")

    assert maybe_build_workspace_trust_prompt(scratch) is None
