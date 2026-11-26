from __future__ import annotations

from uuid import uuid4

from pydantic import JsonValue

from vibe.app_server._utils import now_ms
from vibe.app_server.models import (
    PublicCheckpointEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
)


def rebind_history_with_checkpoint(
    history: list[PublicHistoryEntry],
    session_id: str,
    *,
    kind: str,
    message: str,
    details: JsonValue = None,
) -> list[PublicHistoryEntry]:
    rebound = _rebind_history(history, session_id)
    timestamp = now_ms()
    rebound.append(
        PublicCheckpointEntry(
            id=f"checkpoint:{kind}:{uuid4()}",
            session_id=session_id,
            created_at=timestamp,
            updated_at=timestamp,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            kind=kind,
            message=message,
            details=details,
        )
    )
    return rebound


def _rebind_history(
    history: list[PublicHistoryEntry], session_id: str
) -> list[PublicHistoryEntry]:
    return [entry.model_copy(update={"session_id": session_id}) for entry in history]
