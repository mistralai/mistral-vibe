"""Read-only compatibility access to sessions written by older Vibe versions.

A legacy session is a folder with a ``meta.json`` file and a ``messages.jsonl``
transcript. The Unified runtime still lists those sessions in the picker,
previews them, and imports one into a new Unified session on resume, but the
legacy execution engine is being retired. This module is the one place the
unified list, read, and import paths read legacy session files, so the
retirement can delete the engine without touching user history. Legacy reads
that belong to the legacy escape hatch itself (in ``_runtime.py``) and the
pin/delete cleanup fallbacks stay where they are until that engine is deleted.

Everything here is read-only: the module never writes inside a legacy
session folder, never constructs a legacy runtime object, and treats legacy
files as untrusted input. Pinning and deleting a legacy session are
user-requested cleanup operations and deliberately live elsewhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibe.app_server._history_projection import project_message_history
from vibe.app_server._time import optional_time_ms, time_ms
from vibe.app_server.models import (
    IdleSessionStatus,
    PublicSession,
    PublicSessionState,
    PublicTurnQueue,
)
from vibe.app_server.protocol import SessionReadParams, SessionReadResponse
from vibe.core.config import VibeConfigSchema
from vibe.core.session.resume_sessions import (
    ResumeSessionInfo,
    list_local_resume_sessions,
)
from vibe.core.session.session_interop import (
    InvalidLegacyInteropSourceError,
    export_legacy_committed_history,
    resolve_legacy_session_reference,
)
from vibe.core.session.session_loader import SessionLoader
from vibe.core.types import SessionMetadata

if TYPE_CHECKING:
    from mistralai_vibe_local_harness.vibe import (
        LegacySessionReference as HarnessLegacySessionReference,
        LegacySourceLoader,
        LegacySourceResolver,
    )

__all__ = ["LegacySessionStore"]


class LegacySessionStore:
    """Read-only access to sessions written by older Vibe versions.

    One instance is built from one configuration snapshot, and everything it
    returns reads that snapshot: the surrounding session context pins
    configuration at build time, so the callables agree with the store root
    they were built against even if the configuration is reloaded later. The
    resolver and the loader must come from the same instance: a read that
    resolved legacy sessions differently from a resume would make one session
    id mean two things.
    """

    def __init__(self, config: VibeConfigSchema) -> None:
        self._config = config

    def list_sessions(self, cwd: str | None = None) -> list[PublicSession]:
        """Every legacy session, projected as a row for the merged list.

        Reading the transcript for a preview no caller will show is the
        legacy half's dominant cost, and every consumer renders
        ``title or preview``, so an untitled session reads its first user
        message and a titled one reads nothing.
        """
        return [
            self._public_session(session)
            for session in list_local_resume_sessions(self._config, cwd)
        ]

    def read_session(self, params: SessionReadParams) -> SessionReadResponse | None:
        """The transcript preview for a legacy session not yet imported.

        The unified host only knows the unified store, so a legacy session
        listed by the merged list has no host read. This projection answers
        the picker without a resume or an import. ``None`` means the id names
        no readable legacy session.
        """
        session_dir = SessionLoader.find_session_by_id(
            params.session_id, self._config.session_logging
        )
        if session_dir is None:
            return None
        try:
            messages, metadata_dict = SessionLoader.load_session(session_dir)
        except (OSError, ValueError):
            return None
        if not metadata_dict:
            return None
        metadata = SessionMetadata.model_validate(metadata_dict)
        session_id = metadata.session_id
        history = project_message_history(session_id, messages, metadata)
        # Limit to the requested page size for consistency with the unified path.
        history_limit = params.history.limit if params.history else len(history)
        truncated = history_limit < len(history)
        history = history[-history_limit:] if truncated else history
        return SessionReadResponse(
            state=PublicSessionState(
                event_id=0,
                session=self._public_session(
                    ResumeSessionInfo(
                        session_id=session_id,
                        cwd=metadata.environment.get("working_directory") or "",
                        title=metadata.title,
                        start_time=metadata.start_time,
                        bumped_at=metadata.bumped_at,
                        pinned_at=metadata.pinned_at,
                        updated_at=metadata.start_time,
                        parent_session_id=metadata.parent_session_id,
                    )
                ),
                history=history,
                history_before_cursor=history[0].id if truncated and history else None,
                turns=[],
                turn_queue=PublicTurnQueue(),
            ),
            last_event_id=0,
        )

    def resolver(self) -> LegacySourceResolver:
        """Callable the host uses to map a requested ID to a legacy folder.

        The harness types are imported here rather than at module scope so
        importing this module (and everything that imports it) stays free of
        harness-package imports until a resolver is actually built.
        """
        from mistralai_vibe_local_harness.vibe import (
            LegacySessionReference as HarnessLegacySessionReference,
        )

        session_logging = self._config.session_logging

        def resolve_legacy_source(
            session_id: str,
        ) -> HarnessLegacySessionReference | None:
            reference = resolve_legacy_session_reference(session_id, session_logging)
            if reference is None:
                return None
            return HarnessLegacySessionReference(
                session_id=reference.session_id, cwd=reference.cwd
            )

        return resolve_legacy_source

    def loader(self) -> LegacySourceLoader:
        """Callable the host uses to load a validated committed history.

        The host decides what each state means. ``absent`` is no such session,
        ``invalid`` is a session that exists but cannot be imported safely,
        and ``quiescent`` carries the export the host copies into a new
        Unified session. The harness import stays local for the same reason
        as the resolver's.
        """
        from mistralai_vibe_local_harness.vibe import (
            LegacyImportSource,
            LegacySessionReference as HarnessLegacySessionReference,
        )

        session_logging = self._config.session_logging

        def load_legacy_source(session_id: str) -> LegacyImportSource:
            try:
                export = export_legacy_committed_history(session_id, session_logging)
            except InvalidLegacyInteropSourceError as exc:
                return LegacyImportSource(state="invalid", error=str(exc))
            if export is None:
                return LegacyImportSource(state="absent")
            return LegacyImportSource(
                state="quiescent",
                reference=HarnessLegacySessionReference(
                    session_id=export.reference.session_id, cwd=export.reference.cwd
                ),
                store_revision=export.store_revision,
                history=export.history,
                active_model=export.active_model,
            )

        return load_legacy_source

    def _public_session(self, session: ResumeSessionInfo) -> PublicSession:
        """Project a legacy ``ResumeSessionInfo`` into a ``PublicSession`` row.

        Legacy sessions store timestamps as ISO strings; ``PublicSession``
        expects int milliseconds. ``time_ms`` parses the ISO format and falls
        back to ``now_ms`` on parse failure, so a malformed timestamp never
        breaks the listing.
        """
        return PublicSession(
            id=session.session_id,
            root_session_id=None,
            parent_session_id=session.parent_session_id,
            title=session.title,
            preview=(
                ""
                if session.title
                else SessionLoader.get_first_user_message(
                    session.session_id, self._config.session_logging
                )
            ),
            status=IdleSessionStatus(),
            created_at=time_ms(session.start_time or session.updated_at),
            updated_at=time_ms(session.updated_at),
            bumped_at=optional_time_ms(session.bumped_at),
            pinned_at=optional_time_ms(session.pinned_at),
            cwd=session.cwd or None,
            harness="legacy",
        )
