"""Writes imported conversations directly to the Unified session store."""

from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import cast

from mistralai_vibe_local_harness.session_protocol import SessionReadParams
from mistralai_vibe_local_harness.vibe import (
    ExternalCommittedHistory,
    UnifiedHarnessSessionBackendHost,
)
from vibe._experimental_harness import create_experimental_harness_host
from vibe.app_server._local_projects import (
    LocalProject,
    LocalProjectStore,
    session_project,
)
from vibe.app_server._local_projects_controller import list_local_projects
from vibe.app_server._unified_harness_backend_adapter import (
    _persist_unified_session_metadata,
    _read_unified_session_metadata,
    _unified_session_dir,
)
from vibe.core.config import SessionLoggingConfig
from vibe.core.imports._session import VibeSessionPayload
from vibe.core.session.session_interop import export_messages_to_interop_history
from vibe.core.types import SessionProject

logger = logging.getLogger(__name__)


class UnifiedSessionImportWriter:
    """Creates native Unified sessions without attaching or resuming them."""

    def __init__(
        self,
        session_config: SessionLoggingConfig,
        # None while the local projects flag is off: stamps keep the cloud id.
        local_projects: LocalProjectStore | None = None,
    ) -> None:
        self._session_config = session_config
        self._local_project_store = local_projects
        self._local_projects: list[LocalProject] | None = None

    async def write(self, session: VibeSessionPayload) -> None:
        if not self._session_config.enabled:
            raise ValueError("Vibe session logging must be enabled to import a session")

        host = cast(
            UnifiedHarnessSessionBackendHost, create_experimental_harness_host()
        )
        host.configure_storage(self._session_config.save_dir)
        try:
            session_id = await host.import_committed_history(
                ExternalCommittedHistory(
                    harness=session.source_harness,
                    session_id=session.source_session_id,
                    cwd=str(session.cwd),
                    history=export_messages_to_interop_history(
                        session.messages, source_root=session.cwd
                    ),
                    source_cwd=(
                        str(session.source_cwd)
                        if session.source_cwd is not None
                        else None
                    ),
                    title=session.title,
                    created_at=_milliseconds(session.started_at),
                )
            )
            if session.project_id is not None:
                try:
                    await self._stamp(host, session_id, session.project_id)
                except Exception:
                    # An unstamped session falls back to folder matching.
                    logger.warning("Could not stamp an imported session", exc_info=True)
        finally:
            await host.shutdown()

    async def _stamp(
        self, host: UnifiedHarnessSessionBackendHost, session_id: str, project_id: str
    ) -> None:
        session_dir = _unified_session_dir(self._session_config.save_dir, session_id)
        # Only a fresh import has no metadata: a re-run leaves an opened session alone.
        previous = await asyncio.to_thread(_read_unified_session_metadata, session_dir)
        if previous is not None:
            return
        project = await self._session_project(project_id)
        read = await host.read(
            SessionReadParams(session_id=session_id, history_limit=1)
        )
        await _persist_unified_session_metadata(
            session_dir, read.snapshot.state.session, read.cwd, project=project
        )

    async def _session_project(self, project_id: str) -> SessionProject:
        store = self._local_project_store
        if store is None:
            return SessionProject(kind="cloud", id=project_id)
        if self._local_projects is None:
            self._local_projects = await asyncio.to_thread(list_local_projects, store)
        return session_project(self._local_projects, project_id)


def _milliseconds(timestamp: datetime | None) -> int | None:
    return int(timestamp.timestamp() * 1000) if timestamp is not None else None
