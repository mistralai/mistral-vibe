"""Scratch folders, the working folders of projectless sessions."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

from vibe.app_server.protocol import SessionNoProject, SessionStartParams
from vibe.core.paths import SCRATCH_DIR


def create_scratch_folder() -> Path:
    folder = SCRATCH_DIR.path / str(uuid4())
    folder.mkdir(parents=True)
    return folder.resolve()


def remove_if_empty(folder: Path) -> None:
    with suppress(OSError):
        folder.rmdir()


@asynccontextmanager
async def scratch_folder_for(
    params: SessionStartParams,
) -> AsyncIterator[SessionStartParams]:
    """Runs a projectless start without a cwd in a new scratch folder, removed again if the start fails."""
    if not isinstance(params.project, SessionNoProject) or params.cwd is not None:
        yield params
        return
    folder = await asyncio.to_thread(create_scratch_folder)
    agent_config = params.agent_config.model_copy(update={"cwd": str(folder)})
    try:
        yield params.model_copy(update={"agent_config": agent_config})
    except BaseException:
        await asyncio.to_thread(remove_if_empty, folder)
        raise
