"""Project the Unified Harness's session data into what Vibe's clients see.

A read, a listing or an event from the runtime arrives in the Harness's own
models; the functions here turn it into Vibe's public session, history, turns
and event envelopes. They hold no adapter: the adapter and the Session API path
both call them, so the projection lives here, outside both.
"""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from http import HTTPStatus
import os
from pathlib import Path
from typing import Any, Final, Literal, Protocol, cast, get_args

from mistralai_vibe_local_harness.session_protocol import (
    PublicSession as HarnessPublicSession,
    PublicSessionState as HarnessPublicSessionState,
    SessionReadParams as HarnessSessionReadParams,
    SessionSnapshot as HarnessSessionSnapshot,
    TokenUsage as HarnessTokenUsage,
)
from vibe.app_server._connector_mcp_apps import SessionMcpApps
from vibe.app_server._error_message_cap import bounded_error_message
from vibe.app_server._goal_notice_store import with_goal_notices
from vibe.app_server._harness_errors import raise_unsupported
from vibe.app_server._loop_prompt import project_loop_preview, project_loop_queue
from vibe.app_server._model import ProtocolModel, validate_backend_wire
from vibe.app_server._projector import EventProjector, ProjectedUpdate
from vibe.app_server._session_backend_port import SessionBackendEvent
from vibe.app_server._session_lease_poll import LeaseHolder
from vibe.app_server._session_message import (
    is_session_message,
    project_session_message_preview,
    public_session_message_content,
)
from vibe.app_server._session_seen_state import SessionSeenState
from vibe.app_server._skill_invocation import (
    invoked_skill_payloads,
    public_session_preview,
    skill_invocation_display_names,
    without_skill_invocation_display,
)
from vibe.app_server._state import history_page
from vibe.app_server._time import now_ms
from vibe.app_server._unified_history_projection import (
    project_history_entry,
    project_raw_history_entry,
)
from vibe.app_server._unified_scheduled_loops import ScheduledPrompt
from vibe.app_server._utils import _REFUSED_CREDENTIAL_STATUSES, optional_time_ms
from vibe.app_server.events import (
    HistoryEntryAdded,
    HistoryEntryUpdated,
    SessionSnapshot,
    SessionUpdated,
    StatsUpdated,
    TurnCompleted,
    TurnQueueUpdated,
    TurnRetrying,
    TurnStarted,
)
from vibe.app_server.models import (
    AgentSummary,
    ApprovalPolicySummary,
    ArchivedSessionStatus as VibeArchivedSessionStatus,
    BlockedSessionStatus as VibeBlockedSessionStatus,
    BudgetStopReason,
    EffectDetail,
    EffectState,
    FailedSessionStatus as VibeFailedSessionStatus,
    GenericEffectDetail,
    IdleSessionStatus as VibeIdleSessionStatus,
    PublicBackgroundProcess,
    PublicCallbackEntry,
    PublicEffectEntry,
    PublicError,
    PublicGoal,
    PublicHistoryEntry,
    PublicNoticeEntry,
    PublicRetryCategory,
    PublicRetryState,
    PublicSession,
    PublicSessionProject,
    PublicSessionState,
    PublicSessionWorktree,
    PublicTurn,
    PublicTurnQueue,
    PublicTurnStatus,
    RunningSessionStatus as VibeRunningSessionStatus,
    ScheduledLoop,
    TokenUsage as VibeTokenUsage,
    TurnErrorCode,
)
from vibe.app_server.protocol import (
    HistoryEntryAddedParams,
    HistoryEntryUpdatedParams,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionLeaseHolder,
    SessionListParams,
    SessionReadParams,
    SessionReadResponse,
    SessionSnapshotParams,
    SessionTextContentBlock,
    SessionTurnsListParams,
    SessionTurnsListResponse,
    SessionUpdatedParams,
    TurnCompletedParams,
    TurnContextInputEntry,
    TurnQueueUpdatedParams,
    TurnRetryingParams,
    TurnStartedParams,
    TurnUserInputEntry,
)
from vibe.core.types import SessionMetadata, SessionProject

HISTORY_PAGE_WINDOW = 500
# How many sessions' history windows ``HistoryWindows`` remembers.
_HISTORY_WINDOW_SESSIONS = 64


def metadata_bumped_at_ms(metadata: SessionMetadata | None) -> int | None:
    return optional_time_ms(metadata.bumped_at) if metadata is not None else None


def metadata_pinned_at_ms(metadata: SessionMetadata | None) -> int | None:
    return optional_time_ms(metadata.pinned_at) if metadata is not None else None


def metadata_archived_at_ms(metadata: SessionMetadata | None) -> int | None:
    return optional_time_ms(metadata.archived_at) if metadata is not None else None


# Harness completion-failure codes, in the vocabulary clients are given. A code
# left unmapped reaches them as a string no `TurnErrorCode` branch matches, so
# the turn is treated as an unknown failure.
_HARNESS_ERROR_CODES: Final[dict[str, TurnErrorCode]] = {
    "context_overflow": TurnErrorCode.CONTEXT_TOO_LONG,
    "images_not_supported": TurnErrorCode.IMAGES_NOT_SUPPORTED,
    "modality_not_supported": TurnErrorCode.IMAGES_NOT_SUPPORTED,
    "model_stream_failed": TurnErrorCode.BACKEND_ERROR,
    "model_refused": TurnErrorCode.REFUSAL,
    "model_stream_incomplete": TurnErrorCode.INCOMPLETE_STREAM,
    "model_unauthorized": TurnErrorCode.INVALID_API_KEY,
    "model_credential_unavailable": TurnErrorCode.CREDENTIAL_UNAVAILABLE,
    "pricing_unavailable": TurnErrorCode.PRICING_UNAVAILABLE,
}


# A failed model stream is refined by the HTTP status it failed with.
_STREAM_FAILURE_CODES: Final[dict[int, TurnErrorCode]] = {
    HTTPStatus.TOO_MANY_REQUESTS: TurnErrorCode.RATE_LIMIT,
    HTTPStatus.PAYMENT_REQUIRED: TurnErrorCode.USAGE_LIMIT,
    **dict.fromkeys(_REFUSED_CREDENTIAL_STATUSES, TurnErrorCode.INVALID_API_KEY),
}


@dataclass(frozen=True, slots=True)
class SessionPins:
    """What a session runs, in the ``PublicSession`` fields that already say so.

    Picking the default back writes an empty pin rather than clearing it, so
    every value here is normalised to ``None`` for "follows the default".
    """

    model: str | None = None
    reasoning_effort: str | None = None
    agent: AgentSummary | None = None
    approval_policy: ApprovalPolicySummary | None = None


_NO_SESSION_PINS = SessionPins()


def harness_probe_read_params(params: SessionReadParams) -> HarnessSessionReadParams:
    return HarnessSessionReadParams(
        session_id=params.session_id, history_limit=params.history_limit + 1
    )


def _window_and_older(
    entries: list[dict[str, Any]], history_limit: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split history into the shown window and the entries above it."""
    window = entries[-history_limit:] if history_limit else []
    return window, entries[: len(entries) - len(window)]


def without_probe(
    snapshot: HarnessSessionSnapshot, history_limit: int
) -> HarnessSessionSnapshot:
    """Drop the extra probed entry, recording it as the page's `cursor.before`.

    The Harness trims a read to its limit without setting `cursor.before`, so
    reading one entry more is the only sign that older history exists.
    """
    history = snapshot.state.history
    if len(history.entries) <= history_limit:
        return snapshot
    kept, older = _window_and_older(history.entries, history_limit)
    page = history.model_copy(
        update={
            "entries": kept,
            "cursor": history.cursor.model_copy(update={"before": older[-1]["id"]}),
        }
    )
    return snapshot.model_copy(
        update={
            "state": snapshot.state.model_copy(update={"history": page}),
            "history_limit": history_limit,
        }
    )


class _HistoryWindow(Protocol):
    """A latest window of public history, and where older history resumes."""

    @property
    def history(self) -> list[PublicHistoryEntry] | None: ...

    @property
    def history_before_cursor(self) -> str | None: ...


async def read_widening_window[WindowT: _HistoryWindow](
    read: Callable[[int], Awaitable[WindowT]],
    *,
    start_window: int = HISTORY_PAGE_WINDOW,
    enough: Callable[[list[PublicHistoryEntry]], bool] | None = None,
) -> tuple[WindowT, list[PublicHistoryEntry], int, bool]:
    """Read latest windows, doubling the limit until one fits or history runs out.

    Reads probe one entry past the window, so a missing `history_before_cursor`
    is exact; a read that ignores its limit stops growing instead of looping.
    """
    window = start_window
    loaded = -1
    while True:
        state = await read(window)
        history = state.history or []
        complete = state.history_before_cursor is None or len(history) == loaded
        if complete or (enough is not None and enough(history)):
            return state, history, window, complete
        loaded = len(history)
        window *= 2


class HistoryWindows:
    """Page history out of Harness reads that each return a latest window.

    The Harness cannot read before a cursor, so a page needs a window reaching
    past it. Each session keeps the window its last page needed, so scrolling
    back reads about one window per page instead of doubling up from the tail.
    """

    def __init__(self) -> None:
        self._sizes: dict[str, int] = {}

    async def list(
        self,
        params: SessionHistoryListParams,
        read: Callable[[int], Awaitable[_HistoryWindow]],
    ) -> SessionHistoryListResponse:
        backward = params.sort_direction == "backward"
        before = params.cursor if backward else None
        history, window, complete = await self._read(
            params.session_id, read, before, params.limit
        )
        page = history_page(
            history,
            turn_id=params.turn_id,
            before=before,
            after=None if backward else params.cursor,
            limit=params.limit,
        )
        next_cursor = page.cursor.before if backward else page.cursor.after
        # A window that starts mid-history still has older entries before it.
        if (
            backward
            and not complete
            and page.entries
            and page.entries[0].id == history[0].id
        ):
            next_cursor = page.entries[0].id
        # A complete window still serves the older pages; only the last one forgets it.
        if next_cursor is None:
            self._sizes.pop(params.session_id, None)
        else:
            self._remember(params.session_id, window)
        return SessionHistoryListResponse(
            items=page.entries,
            next_cursor=next_cursor,
            previous_cursor=page.cursor.after if backward else page.cursor.before,
        )

    async def _read(
        self,
        session_id: str,
        read: Callable[[int], Awaitable[_HistoryWindow]],
        before: str | None,
        limit: int,
    ) -> tuple[list[PublicHistoryEntry], int, bool]:
        def window_reaches(history: list[PublicHistoryEntry]) -> bool:
            end = (
                len(history)
                if before is None
                else next(
                    (
                        index
                        for index, entry in enumerate(history)
                        if entry.id == before
                    ),
                    None,
                )
            )
            return end is not None and end >= limit

        _, history, window, complete = await read_widening_window(
            read,
            start_window=self._sizes.get(session_id, HISTORY_PAGE_WINDOW),
            enough=window_reaches,
        )
        return history, window, complete

    def _remember(self, session_id: str, window: int) -> None:
        """Keep the most recently paged sessions' windows, evicting the oldest."""
        self._sizes.pop(session_id, None)
        self._sizes[session_id] = window
        if len(self._sizes) > _HISTORY_WINDOW_SESSIONS:
            del self._sizes[next(iter(self._sizes))]


@dataclass(frozen=True, slots=True)
class _ProjectedEntry:
    source: Mapping[str, Any]
    history: list[PublicHistoryEntry]


# Projection is pure and public entries are never mutated, so states share them.
class HistoryProjections:
    def __init__(self) -> None:
        self._by_id: dict[str, _ProjectedEntry] = {}
        self._display_roots: tuple[Path, ...] = ()

    def project(
        self, window: Sequence[Mapping[str, Any]], display_roots: tuple[Path, ...]
    ) -> list[PublicHistoryEntry]:
        previous, self._by_id = self._by_id, {}
        # Displayed paths depend on the roots, so a moved workspace reprojects.
        if display_roots != self._display_roots:
            previous, self._display_roots = {}, display_roots
        history: list[PublicHistoryEntry] = []
        for raw_entry in window:
            entry_id = raw_entry.get("id")
            if not isinstance(entry_id, str):
                history.extend(project_raw_history_entry(raw_entry, display_roots))
                continue
            projected = previous.get(entry_id)
            if projected is None or projected.source is not raw_entry:
                public_entries = (
                    projected.history
                    if projected is not None and projected.source == raw_entry
                    else project_raw_history_entry(raw_entry, display_roots)
                )
                # The Harness rebuilds an entry once when it persists it, then
                # repeats that object: keep the newer one to match it by identity.
                projected = _ProjectedEntry(raw_entry, public_entries)
            self._by_id[entry_id] = projected
            history.extend(projected.history)
        return history


def lease_holder(holder: LeaseHolder | None) -> SessionLeaseHolder | None:
    return None if holder is None else SessionLeaseHolder(process_id=holder.process_id)


@dataclass(frozen=True, slots=True)
class ProjectedHistory:
    """Public history already projected, and whether older entries exist."""

    entries: list[PublicHistoryEntry]
    has_older: bool
    apps_linked: bool = False
    """Whether ``entries`` already link their MCP app routes."""


def read_response(  # noqa: PLR0913 - one read input per keyword, all keyword-only
    snapshot: HarnessSessionSnapshot,
    cwd: str | None,
    *,
    event_id: int | None = None,
    metadata: SessionMetadata | None = None,
    pins: SessionPins = _NO_SESSION_PINS,
    worktree: PublicSessionWorktree | None = None,
    projections: HistoryProjections | None = None,
    workspace_roots: Sequence[Path] = (),
    # Required on purpose: a caller that forgets the session's loops or starter
    # must fail loudly instead of silently publishing a state without them.
    scheduled_loops: Sequence[ScheduledPrompt],
    # Required for the same reason: None means the session has no goal.
    goal: PublicGoal | None,
    # The goal's transcript notices, which the app server keeps (empty for none).
    goal_notices: Sequence[PublicNoticeEntry],
    # Required for the same reason: None means the session runs no connector
    # routes here (a subagent, or a session that is not open).
    mcp_apps: SessionMcpApps | None,
    started_by_session_id: str | None,
    projected_history: ProjectedHistory | None = None,
) -> SessionReadResponse:
    display_roots = display_roots_for(cwd, workspace_roots)
    if projected_history is not None:
        # Already projected from Session API entries, one entry at a time.
        history = projected_history.entries
        has_older = projected_history.has_older
        if projected_history.apps_linked:
            mcp_apps = None
    else:
        entries = snapshot.state.history.entries
        history_limit = getattr(snapshot, "history_limit", len(entries))
        window, older = _window_and_older(entries, history_limit)
        has_older = bool(older) or snapshot.state.history.cursor.before is not None
        history = (
            [
                entry
                for raw_entry in window
                for entry in project_raw_history_entry(raw_entry, display_roots)
            ]
            if projections is None
            else projections.project(window, display_roots)
        )
    history = with_app_server_entries(
        history, has_older=has_older, goal_notices=goal_notices, mcp_apps=mcp_apps
    )
    last_event_id = snapshot.watermark if event_id is None else event_id
    harness_retrying = getattr(snapshot.state, "retrying", None)
    state = PublicSessionState(
        event_id=last_event_id,
        session=public_session(
            snapshot.state.session,
            cwd,
            metadata=metadata,
            pins=pins,
            worktree=worktree,
            scheduled_loops=scheduled_loops,
            goal=goal,
            started_by_session_id=started_by_session_id,
        ),
        history_before_cursor=history[0].id if history and has_older else None,
        turns=(
            [public_turn(snapshot.state.latest_turn)]
            if snapshot.state.latest_turn is not None
            else []
        ),
        active_callbacks=_public_active_callbacks(
            snapshot.state.active_callbacks, display_roots
        ),
        turn_queue=public_turn_queue(snapshot.state.turn_queue),
        background_processes=[
            validate_backend_wire(
                PublicBackgroundProcess, process.model_dump(mode="json", by_alias=True)
            )
            # A harness version predating public process summaries exposes no
            # background_processes attribute on its session-state model.
            for process in getattr(snapshot.state, "background_processes", [])
        ],
        retrying=(
            PublicRetryState(
                turn_id=harness_retrying.turn_id,
                category=PublicRetryCategory(harness_retrying.category),
                detail=harness_retrying.detail,
                retry_at=harness_retrying.retry_at,
                retry_attempt=harness_retrying.retry_attempt,
            )
            if harness_retrying is not None
            else None
        ),
    )
    # The entries are validated models already: validating them again would
    # cost each update the length of the history.
    state = state.model_copy(update={"history": history})
    return SessionReadResponse(state=state, last_event_id=last_event_id)


def with_app_server_entries(
    history: list[PublicHistoryEntry],
    *,
    has_older: bool,
    goal_notices: Sequence[PublicNoticeEntry],
    mcp_apps: SessionMcpApps | None,
) -> list[PublicHistoryEntry]:
    """A window of the Runtime's history with what the app server adds to it.

    The app server keeps goal notices itself and links MCP app routes to their
    tool effects; ``has_older`` says whether older history exists.
    """
    history = with_goal_notices(history, goal_notices, complete=not has_older)
    if mcp_apps is not None:
        history = mcp_apps.apply(history)
    return history


def is_legacy_stop(entry: PublicHistoryEntry) -> bool:
    """Whether ``entry`` is a deprecated ``subagent.stop`` effect.

    It names its child by agent name, matched against the spawns before it.
    """
    return (
        isinstance(entry, PublicEffectEntry)
        and isinstance(entry.detail, GenericEffectDetail)
        and entry.detail.tool_name == "subagent.stop"
    )


def with_callbacks_in_history(state: PublicSessionState) -> PublicSessionState:
    """``state`` with its open callbacks as history entries, as a resync needs."""
    if not state.active_callbacks:
        return state
    history = {entry.id: entry for entry in state.history or []}
    for callback in state.active_callbacks:
        history[callback.id] = callback
    return state.model_copy(update={"history": list(history.values())})


def display_roots_for(
    cwd: str | None, workspace_roots: Sequence[Path]
) -> tuple[Path, ...]:
    """Where displayed file paths are made relative: the cwd first, then the roots."""
    roots = [Path(os.path.normpath(cwd))] if cwd else []
    return tuple(dict.fromkeys([*roots, *workspace_roots]))


def _public_active_callbacks(
    raw_callbacks: Sequence[object], display_roots: Sequence[Path]
) -> list[PublicCallbackEntry]:
    callbacks: list[PublicCallbackEntry] = []
    for raw_callback in raw_callbacks:
        callback = project_history_entry(raw_callback, display_roots)
        if not isinstance(callback, PublicCallbackEntry):
            raise_unsupported("an active Harness callback with a non-callback entry")
        callbacks.append(callback)
    return callbacks


def harness_event_watermark(event: dict[str, Any]) -> int:
    event_id = event.get("eventId")
    if isinstance(event_id, int):
        return event_id
    if isinstance(event_id, str) and event_id.isdecimal():
        return int(event_id)
    raise_unsupported("a Harness session event without an event id")


def published_harness_state(raw_state: dict[str, Any]) -> HarnessPublicSessionState:
    # Window entries are validated when projected; validating the whole history
    # on every update would cost the session's length.
    raw_history = raw_state.get("history")
    entries = raw_history.get("entries") if isinstance(raw_history, dict) else None
    if not isinstance(raw_history, dict) or not (
        isinstance(entries, list) and all(isinstance(entry, dict) for entry in entries)
    ):
        return HarnessPublicSessionState.model_validate(raw_state)
    state = HarnessPublicSessionState.model_validate({
        **raw_state,
        "history": {**raw_history, "entries": []},
    })
    return state.model_copy(
        update={"history": state.history.model_copy(update={"entries": entries})}
    )


def _vibe_token_usage(usage: HarnessTokenUsage | None) -> VibeTokenUsage | None:
    """Harness token usage in the client model, ignoring fields it adds (ADR 0014)."""
    if usage is None:
        return None
    return validate_backend_wire(
        VibeTokenUsage, usage.model_dump(mode="json", by_alias=True)
    )


def public_session(
    session: HarnessPublicSession,
    cwd: str | None,
    *,
    harness: Literal["legacy", "unified"] = "unified",
    metadata: SessionMetadata | None = None,
    pins: SessionPins = _NO_SESSION_PINS,
    worktree: PublicSessionWorktree | None = None,
    scheduled_loops: Sequence[ScheduledPrompt] = (),
    goal: PublicGoal | None = None,
    started_by_session_id: str | None,
) -> PublicSession:
    status = cast(Any, session.status)
    status_type = getattr(status, "type", None)
    if status_type == "running":
        public_status = VibeRunningSessionStatus(active_turn_id=status.active_turn_id)
    elif status_type == "blocked":
        public_status = VibeBlockedSessionStatus(
            active_turn_id=status.active_turn_id,
            callback_id=status.callback_id,
            reason=status.callback_kind,
        )
    elif status_type == "failed":
        public_status = VibeFailedSessionStatus(
            message=bounded_error_message(status.message)
        )
    elif status_type == "archived":
        public_status = VibeArchivedSessionStatus()
    else:
        public_status = VibeIdleSessionStatus()
    return PublicSession(
        id=session.id,
        root_session_id=session.root_session_id,
        parent_session_id=session.parent_session_id,
        started_by_session_id=started_by_session_id,
        title=session.title,
        preview=project_session_message_preview(
            project_loop_preview(public_session_preview(session.preview))
        ),
        status=public_status,
        created_at=session.created_at,
        updated_at=session.updated_at,
        bumped_at=metadata_bumped_at_ms(metadata),
        pinned_at=metadata_pinned_at_ms(metadata),
        archived_at=metadata_archived_at_ms(metadata),
        is_unseen=SessionSeenState.from_metadata(metadata).is_unseen,
        cwd=cwd,
        worktree=worktree,
        model=pins.model,
        reasoning_effort=pins.reasoning_effort,
        agent=pins.agent,
        approval_policy=pins.approval_policy,
        token_usage=_vibe_token_usage(session.token_usage),
        context_usage=_vibe_token_usage(session.context_usage),
        harness=harness,
        loops=[project_scheduled_loop(loop) for loop in scheduled_loops],
        goal=goal,
        project=(
            public_session_project(metadata.project)
            if metadata is not None and metadata.project is not None
            else None
        ),
        owes_reply=metadata is not None and bool(metadata.owed_replies),
        repository_root=metadata.repository_root if metadata is not None else None,
        checkout_root=metadata.checkout_root if metadata is not None else None,
    )


def public_session_project(project: SessionProject) -> PublicSessionProject:
    return PublicSessionProject(kind=project.kind, id=project.id)


def stored_session_project(session: PublicSession) -> SessionProject | None:
    if session.project is None:
        return None
    return SessionProject(kind=session.project.kind, id=session.project.id)


def merge_listed_sessions(
    params: SessionListParams,
    unified: Sequence[PublicSession],
    legacy: Sequence[PublicSession],
) -> list[PublicSession]:
    merged = [*unified, *legacy]
    if params.session_ids is not None:
        asked = set(params.session_ids)
        merged = [session for session in merged if session.id in asked]
    if not params.include_archived:
        merged = [session for session in merged if session.archived_at is None]
    if params.owes_reply:
        merged = [session for session in merged if session.owes_reply]
    merged.sort(key=lambda session: (session.updated_at, session.id), reverse=True)
    return merged


def encode_merged_cursor(session: PublicSession) -> str:
    """Encode a cursor for the merged list from (updated_at, session_id)."""
    return base64.b64encode(f"{session.updated_at}:{session.id}".encode()).decode()


def merged_cursor_index(merged: list[PublicSession], cursor: str | None) -> int:
    """Where the page after ``cursor`` starts in the merged list.

    The list is sorted by ``(updated_at, id)`` descending and the cursor names
    the previous page's last item. No cursor, or a malformed one, starts at 0.
    """
    if cursor is None:
        return 0
    try:
        updated_str, _, cursor_id = base64.b64decode(cursor).decode().partition(":")
        cursor_updated_at = int(updated_str)
    except (ValueError, UnicodeDecodeError):
        return 0
    for i, session in enumerate(merged):
        if session.updated_at < cursor_updated_at:
            return i
        if session.updated_at == cursor_updated_at and session.id < cursor_id:
            return i
    return len(merged)


def project_scheduled_loop(loop: ScheduledPrompt) -> ScheduledLoop:
    return ScheduledLoop(
        id=loop.id,
        prompt=loop.prompt,
        interval_seconds=loop.interval_seconds,
        cron=loop.cron,
        next_fire_at=loop.next_fire_at,
    )


_BUDGET_STOP_REASONS: Final = frozenset(get_args(BudgetStopReason))


def public_turn(turn: object) -> PublicTurn:
    raw = cast(Any, turn)
    error = getattr(raw, "error", None)
    reason = getattr(raw, "reason", None)
    return PublicTurn(
        id=raw.id,
        session_id=raw.session_id,
        status=PublicTurnStatus(str(raw.status)),
        started_at=raw.started_at,
        completed_at=getattr(raw, "completed_at", None),
        error=_public_turn_error(error),
        stop_reason=getattr(raw, "stop_reason", None),
        budget_stop_reason=reason if reason in _BUDGET_STOP_REASONS else None,
        queue_item_id=getattr(raw, "queue_item_id", None),
        input_entry_id=getattr(raw, "input_entry_id", None),
        skip_reason=getattr(raw, "skip_reason", None),
        reject_reason=getattr(raw, "reject_reason", None),
    )


def public_turn_queue(queue: object) -> PublicTurnQueue:
    raw = cast(Any, queue)
    projected = project_loop_queue(
        validate_backend_wire(
            PublicTurnQueue, raw.model_dump(mode="json", by_alias=True)
        )
    )
    return projected.model_copy(
        update={
            "items": [
                item.model_copy(
                    update={
                        "entries": [
                            _project_queued_entry(entry) for entry in item.entries
                        ]
                    }
                )
                for item in projected.items
            ]
        }
    )


def _project_queued_entry(
    entry: TurnContextInputEntry | TurnUserInputEntry,
) -> TurnContextInputEntry | TurnUserInputEntry:
    if not isinstance(entry, TurnUserInputEntry):
        return entry
    entry = entry.model_copy(
        update={
            "content": public_session_message_content(
                entry.content,
                is_session_message(entry.annotations.vibe_user_display_content),
            )
        }
    )
    display_names = skill_invocation_display_names(
        entry.annotations.vibe_user_display_content
    )
    public_annotations = entry.annotations.model_copy(
        update={
            "vibe_user_display_content": without_skill_invocation_display(
                entry.annotations.vibe_user_display_content
            )
        }
    )
    payloads = invoked_skill_payloads(
        [
            block.text if isinstance(block, SessionTextContentBlock) else None
            for block in entry.content
        ],
        display_names,
    )
    indices = {index for index, _name in payloads}
    return entry.model_copy(
        update={
            "content": [
                block
                for index, block in enumerate(entry.content)
                if index not in indices
            ],
            "annotations": public_annotations,
        }
    )


def _public_turn_error(error: object | None) -> PublicError | None:
    if error is None:
        return None
    raw_error = cast(Any, error)
    public_error = validate_backend_wire(
        PublicError, raw_error.model_dump(mode="json", by_alias=True)
    )
    public_error = public_error.model_copy(
        update={"message": bounded_error_message(public_error.message)}
    )
    # The Harness has its own error vocabulary; clients only know TurnErrorCode.
    mapped = _HARNESS_ERROR_CODES.get(str(public_error.code))
    if public_error.code == "model_stream_failed":
        details = public_error.details if isinstance(public_error.details, dict) else {}
        status = details.get("httpStatus")
        if isinstance(status, int):
            mapped = _STREAM_FAILURE_CODES.get(status, mapped)
    if mapped:
        public_error = public_error.model_copy(update={"code": mapped})
    return public_error


def turns_list_response(
    turns: list[PublicTurn], params: SessionTurnsListParams
) -> SessionTurnsListResponse:
    if params.sort_direction == "backward":
        if params.cursor is None:
            page = turns[-params.limit :]
            first_index = max(0, len(turns) - len(page))
        else:
            end = next(
                (index for index, turn in enumerate(turns) if turn.id == params.cursor),
                0,
            )
            first_index = max(0, end - params.limit)
            page = turns[first_index:end]
        last_index = first_index + len(page) - 1
    else:
        first_index = (
            0
            if params.cursor is None
            else next(
                (
                    index + 1
                    for index, turn in enumerate(turns)
                    if turn.id == params.cursor
                ),
                len(turns),
            )
        )
        page = turns[first_index : first_index + params.limit]
        last_index = first_index + len(page) - 1
    next_cursor = page[0].id if page and first_index > 0 else None
    previous_cursor = page[-1].id if page and last_index < len(turns) - 1 else None
    if params.sort_direction == "forward":
        next_cursor, previous_cursor = previous_cursor, next_cursor
    return SessionTurnsListResponse(
        items=page, next_cursor=next_cursor, previous_cursor=previous_cursor
    )


def turns_from_history(
    history: Sequence[PublicHistoryEntry], session_id: str
) -> list[PublicTurn]:
    turns: dict[str, PublicTurn] = {}
    for entry in history:
        if entry.turn_id is None:
            continue
        previous = turns.get(entry.turn_id)
        input_entry_id = (
            entry.input_entry_id
            if entry.input_entry_id is not None
            else previous.input_entry_id
            if previous is not None
            else None
        )
        turns[entry.turn_id] = PublicTurn(
            id=entry.turn_id,
            session_id=session_id,
            status=PublicTurnStatus.COMPLETED,
            started_at=entry.created_at if previous is None else previous.started_at,
            completed_at=entry.updated_at,
            input_entry_id=input_entry_id,
        )
    return list(turns.values())


class ManualShellEffect:
    """The ``shell`` history entry behind one manual `!` command.

    ``EventProjector`` already owns the shape of an effect entry and the patches
    that advance it -- ``TurnController`` drives it exactly this way for the
    legacy backend. One projector per command is enough here, because a manual
    command is the only thing the Host ever writes into a Unified timeline.
    """

    def __init__(self, projector: EventProjector, entry_id: str) -> None:
        self._projector = projector
        self._entry_id = entry_id
        self._output: list[str] = []
        self._terminal_state: EffectState | None = None

    @property
    def output_text(self) -> str:
        return "".join(self._output)

    def start(self, detail: EffectDetail) -> HistoryEntryAdded:
        self._projector.start_effect(self._entry_id, title="shell", detail=detail)
        return HistoryEntryAdded(self._entry)

    def append_output(self, chunk: str) -> HistoryEntryUpdated:
        self._output.append(chunk)
        previous = self._entry
        return self._updated(
            previous, self._projector.append_effect_output(self._entry_id, chunk)
        )

    def complete(self, state: EffectState) -> HistoryEntryUpdated:
        previous = self._entry
        self._terminal_state = state
        return self._updated(
            previous, self._projector.complete_effect(self._entry_id, state)
        )

    @property
    def entry(self) -> PublicEffectEntry:
        """The entry in its current, terminal-when-completed form."""
        return cast(PublicEffectEntry, self._entry)

    @property
    def record(self) -> PublicEffectEntry:
        """The completed entry with the full terminal state, before the client cap.

        The store applies its own, tighter model-output limit; recording the
        client-capped state instead could leave it nothing structured to cap.
        """
        if self._terminal_state is None:
            return self.entry
        return self.entry.model_copy(update={"state": self._terminal_state})

    @property
    def _entry(self) -> PublicHistoryEntry:
        return self._projector.history[-1]

    def _updated(
        self, previous: PublicHistoryEntry, update: ProjectedUpdate
    ) -> HistoryEntryUpdated:
        params = cast(HistoryEntryUpdatedParams, update.params)
        return HistoryEntryUpdated(
            previous=previous, entry=self._entry, patch=params.patch
        )


def event_envelope(event: object, event_id: int) -> SessionBackendEvent:
    emitted_at = now_ms()
    params: ProtocolModel
    if isinstance(event, HistoryEntryAdded):
        method = "history/entryAdded"
        session_id = event.entry.session_id
        params = HistoryEntryAddedParams(
            event_id=event_id,
            session_id=session_id,
            emitted_at=emitted_at,
            turn_id=event.entry.turn_id,
            entry=event.entry,
        )
    elif isinstance(event, HistoryEntryUpdated):
        method = "history/entryUpdated"
        session_id = event.entry.session_id
        params = HistoryEntryUpdatedParams(
            event_id=event_id,
            session_id=session_id,
            emitted_at=emitted_at,
            turn_id=event.entry.turn_id,
            entry_id=event.entry.id,
            patch=event.patch,
        )
    elif isinstance(event, SessionUpdated):
        method = "session/updated"
        session_id = event.session.id
        params = SessionUpdatedParams(
            event_id=event_id,
            session_id=session_id,
            emitted_at=emitted_at,
            patch=event.patch,
        )
    elif isinstance(event, TurnStarted | TurnCompleted):
        started = isinstance(event, TurnStarted)
        method = "turn/started" if started else "turn/completed"
        session_id = event.turn.session_id
        params = (TurnStartedParams if started else TurnCompletedParams)(
            event_id=event_id,
            session_id=session_id,
            emitted_at=emitted_at,
            turn=event.turn,
        )
    elif isinstance(event, StatsUpdated):
        method = "session/statsUpdated"
        session_id = event.params.session_id
        params = event.params.model_copy(update={"event_id": event_id})
    else:
        raise TypeError(f"Unsupported app-server event: {event!r}")
    return SessionBackendEvent(
        event=event,
        method=method,
        params=params,
        session_id=session_id,
        event_id=event_id,
    )


def with_history_entry(
    state: PublicSessionState, entry: PublicHistoryEntry
) -> PublicSessionState:
    history = state.history or []
    if any(existing.id == entry.id for existing in history):
        history = [
            entry if existing.id == entry.id else existing for existing in history
        ]
    else:
        history = [*history, entry]
    return state.model_copy(update={"history": history})


def snapshot_event(state: PublicSessionState) -> SessionBackendEvent:
    params = SessionSnapshotParams(
        event_id=state.event_id,
        session_id=state.session.id,
        emitted_at=now_ms(),
        state=state,
    )
    return SessionBackendEvent(
        event=SessionSnapshot(state),
        method="session/snapshot",
        params=params,
        session_id=state.session.id,
        event_id=state.event_id,
    )


def retrying_event(session_id: str, retrying: PublicRetryState) -> SessionBackendEvent:
    params = TurnRetryingParams(
        session_id=session_id, category=retrying.category, detail=retrying.detail
    )
    return SessionBackendEvent(
        event=TurnRetrying(params),
        method="turn/retrying",
        params=params,
        session_id=session_id,
    )


def turn_queue_event_envelope(
    queue: PublicTurnQueue, event_id: int, session_id: str
) -> SessionBackendEvent:
    params = TurnQueueUpdatedParams(
        event_id=event_id, session_id=session_id, emitted_at=now_ms(), queue=queue
    )
    return SessionBackendEvent(
        event=TurnQueueUpdated(queue),
        method="turn/queueUpdated",
        params=params,
        session_id=session_id,
        event_id=event_id,
    )


__all__ = [
    "HISTORY_PAGE_WINDOW",
    "HistoryProjections",
    "HistoryWindows",
    "ManualShellEffect",
    "ProjectedHistory",
    "SessionPins",
    "display_roots_for",
    "encode_merged_cursor",
    "event_envelope",
    "harness_event_watermark",
    "harness_probe_read_params",
    "is_legacy_stop",
    "lease_holder",
    "merge_listed_sessions",
    "merged_cursor_index",
    "metadata_archived_at_ms",
    "metadata_bumped_at_ms",
    "metadata_pinned_at_ms",
    "project_scheduled_loop",
    "public_session",
    "public_session_project",
    "public_turn",
    "public_turn_queue",
    "published_harness_state",
    "read_response",
    "read_widening_window",
    "retrying_event",
    "snapshot_event",
    "stored_session_project",
    "turn_queue_event_envelope",
    "turns_from_history",
    "turns_list_response",
    "with_app_server_entries",
    "with_callbacks_in_history",
    "with_history_entry",
    "without_probe",
]
