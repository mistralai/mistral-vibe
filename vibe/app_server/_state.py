from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from vibe.app_server._projection import (
    project_agents,
    project_message_history,
    project_workdir,
)
from vibe.app_server._utils import now_ms
from vibe.app_server.models import (
    BlockedSessionStatus,
    IdleSessionStatus,
    PublicCallbackEntry,
    PublicHistoryEntry,
    PublicHistoryPage,
    PublicSession,
    PublicSessionState,
    PublicTurn,
    RunningSessionStatus,
    TokenUsage,
)
from vibe.core.agent_loop import AgentLoop
from vibe.core.types import LLMMessage, Role, SessionMetadata


def build_public_state(
    agent_loop: AgentLoop,
    *,
    history: list[PublicHistoryEntry],
    current_history: list[PublicHistoryEntry],
    callbacks: list[PublicCallbackEntry],
    active_turn: PublicTurn | None,
    last_turn: PublicTurn | None,
    history_limit: int,
) -> PublicSessionState:
    all_history = [*history, *current_history]
    open_callbacks = [
        callback for callback in callbacks if callback.state.status == "open"
    ]
    if active_turn is None:
        status = IdleSessionStatus()
    elif open_callbacks:
        callback = open_callbacks[-1]
        status = BlockedSessionStatus(
            active_turn_id=active_turn.id,
            callback_id=callback.callback_id,
            reason=callback.detail.kind,
        )
    else:
        status = RunningSessionStatus(active_turn_id=active_turn.id)
    metadata = agent_loop.session_logger.session_metadata
    created_at = _parse_time_ms(metadata.start_time) if metadata else now_ms()
    try:
        model = agent_loop.config.get_active_model().alias
    except ValueError:
        model = None
    workdir = project_workdir(agent_loop)
    active_agent, _ = project_agents(agent_loop)
    session = PublicSession(
        id=agent_loop.session_id,
        root_session_id=agent_loop.parent_session_id or agent_loop.session_id,
        parent_session_id=agent_loop.parent_session_id,
        title=agent_loop.session_logger.title,
        preview=session_preview(agent_loop),
        status=status,
        created_at=created_at,
        updated_at=now_ms(),
        cwd=workdir,
        workspace_roots=[
            str(root) for root in agent_loop.harness_files.workspace_roots
        ],
        model=model,
        agent=active_agent,
        token_usage=token_usage(agent_loop),
    )
    return PublicSessionState(
        event_id=0,
        session=session,
        history=history_page(all_history, limit=history_limit),
        active_callbacks=open_callbacks,
        latest_turn=active_turn or last_turn,
    )


def build_stored_public_state(
    session_id: str,
    messages: Sequence[LLMMessage],
    metadata: SessionMetadata,
    *,
    history_limit: int,
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
            created_at=_parse_time_ms(metadata.start_time),
            updated_at=(
                _parse_time_ms(metadata.end_time)
                if metadata.end_time is not None
                else now_ms()
            ),
            cwd=cwd,
        ),
        history=history_page(history, limit=history_limit),
        active_callbacks=[],
        latest_turn=None,
    )


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


def token_usage(agent_loop: AgentLoop) -> TokenUsage:
    stats = agent_loop.stats
    return TokenUsage(
        input_tokens=stats.session_prompt_tokens,
        output_tokens=stats.session_completion_tokens,
        total_tokens=stats.session_total_llm_tokens,
    )


def session_preview(agent_loop: AgentLoop) -> str:
    return message_preview(agent_loop.messages)


def message_preview(messages: Sequence[LLMMessage]) -> str:
    return next(
        (
            message.content[:160]
            for message in messages
            if message.role is Role.user and message.content
        ),
        "",
    )


def _parse_time_ms(value: str) -> int:
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return now_ms()
