from __future__ import annotations

from collections.abc import Sequence

from vibe.app_server._history_projection import project_message_history
from vibe.app_server._utils import now_ms, optional_time_ms, time_ms
from vibe.app_server.models import (
    IdleSessionStatus,
    PublicHistoryEntry,
    PublicHistoryPage,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    PublicTurnStatus,
)
from vibe.core.types import LLMMessage, Role, SessionMetadata


def build_stored_public_state(
    session_id: str,
    messages: Sequence[LLMMessage],
    metadata: SessionMetadata,
    *,
    history_limit: int,
    turns_limit: int | None = None,
    include_history: bool = True,
    include_turns: bool = True,
) -> PublicSessionState:
    history = project_message_history(session_id, messages, metadata)
    cwd = metadata.environment.get("working_directory")
    return PublicSessionState(
        event_id=0,
        session=PublicSession(
            id=session_id,
            root_session_id=metadata.parent_session_id or session_id,
            parent_session_id=metadata.parent_session_id,
            title=metadata.title,
            preview=message_preview(messages),
            status=IdleSessionStatus(),
            created_at=time_ms(metadata.start_time),
            updated_at=(
                time_ms(metadata.end_time)
                if metadata.end_time is not None
                else now_ms()
            ),
            bumped_at=optional_time_ms(metadata.bumped_at),
            archived_at=optional_time_ms(metadata.archived_at),
            cwd=cwd,
        ),
        history=history[-history_limit:] if include_history else None,
        history_before_cursor=(
            history[-history_limit].id
            if include_history and len(history) > history_limit
            else None
        ),
        turns=(
            _turns_from_history(history, session_id)[-(turns_limit or history_limit) :]
            if include_turns
            else None
        ),
        active_callbacks=[],
    )


def _turns_from_history(
    history: Sequence[PublicHistoryEntry], session_id: str
) -> list[PublicTurn]:
    turns: dict[str, PublicTurn] = {}
    for entry in history:
        if entry.turn_id is None:
            continue
        previous = turns.get(entry.turn_id)
        turns[entry.turn_id] = PublicTurn(
            id=entry.turn_id,
            session_id=session_id,
            status=PublicTurnStatus.COMPLETED,
            started_at=entry.created_at if previous is None else previous.started_at,
            completed_at=entry.updated_at,
        )
    return list(turns.values())


def history_page(
    entries: list[PublicHistoryEntry],
    *,
    turn_id: str | None = None,
    before: str | None = None,
    after: str | None = None,
    limit: int,
) -> PublicHistoryPage:
    if turn_id is not None:
        entries = [entry for entry in entries if entry.turn_id == turn_id]
    if after is not None:
        start = next(
            (index + 1 for index, entry in enumerate(entries) if entry.id == after),
            len(entries),
        )
        page = entries[start : start + limit]
        range_name = "page"
    elif before is not None:
        end = next(
            (index for index, entry in enumerate(entries) if entry.id == before), 0
        )
        page = entries[max(0, end - limit) : end]
        range_name = "page"
    else:
        page = entries[-limit:]
        range_name = "latest"
    first_index = entries.index(page[0]) if page else -1
    last_index = entries.index(page[-1]) if page else -1
    return PublicHistoryPage.model_validate({
        "entries": page,
        "cursor": {
            "before": page[0].id if first_index > 0 else None,
            "after": page[-1].id if 0 <= last_index < len(entries) - 1 else None,
        },
        "range": range_name,
    })


def message_preview(messages: Sequence[LLMMessage]) -> str:
    return next(
        (
            message.content[:160]
            for message in messages
            # Skips injected context: a manual shell summary is a user-role
            # message the user never typed.
            if message.role is Role.user and message.content and not message.injected
        ),
        "",
    )
