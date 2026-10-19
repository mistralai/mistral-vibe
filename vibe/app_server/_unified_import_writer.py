"""Writes imported conversations directly to the Unified session store."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from mistralai_vibe_local_harness.vibe import (
    ExternalCommittedHistory,
    UnifiedHarnessSessionBackendHost,
)
from vibe._experimental_harness import create_experimental_harness_host
from vibe.core.config import SessionLoggingConfig
from vibe.core.imports._session import VibeSessionPayload
from vibe.core.session.session_interop import export_messages_to_interop_history


class UnifiedSessionImportWriter:
    """Creates native Unified sessions without attaching or resuming them."""

    def __init__(self, session_config: SessionLoggingConfig) -> None:
        self._session_config = session_config

    async def write(self, session: VibeSessionPayload) -> None:
        if not self._session_config.enabled:
            raise ValueError("Vibe session logging must be enabled to import a session")

        host = cast(
            UnifiedHarnessSessionBackendHost, create_experimental_harness_host()
        )
        host.configure_storage(self._session_config.save_dir)
        try:
            await host.import_committed_history(
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
        finally:
            await host.shutdown()


def _milliseconds(timestamp: datetime | None) -> int | None:
    return int(timestamp.timestamp() * 1000) if timestamp is not None else None
