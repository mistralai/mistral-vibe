"""The adapter's Session API path: reads, events and compaction.

``SessionApiPath`` holds what the adapter needs only when a Session talks to
the local runtime through the Session API: the stream followers, each
Session's API state, the compaction hold and the reads waiting on the stream.
It translates through the adapter it belongs to, so both paths share the
snapshot translation, callbacks, telemetry and child summaries.

The adapter imports this module only on the Session API path, so a Session on
the direct path does not load the Session API.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any, cast

from pydantic import TypeAdapter

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    HistoryEntry,
    Page,
    PageRequest as ApiPageRequest,
    ToolResultHistoryEntry,
    TurnState,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    CallbacksTreeParams,
    CallbacksTreeResponse,
    EventsReadParams,
    PluginInfo as ApiPluginInfo,
    PluginInfoParams as ApiPluginInfoParams,
    PluginInfoResponse as ApiPluginInfoResponse,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionReadParams as ApiSessionReadParams,
    SessionReadResponse as ApiSessionReadResponse,
    SessionTurnsListParams as ApiTurnsListParams,
    SessionTurnsListResponse as ApiTurnsListResponse,
)
from mistralai_vibe_local_harness.session_protocol import (
    PublicSessionState as HarnessPublicSessionState,
    PublicTurn as HarnessPublicTurn,
    SessionReadParams as HarnessSessionReadParams,
    SessionSnapshot as HarnessSessionSnapshot,
)
from mistralai_vibe_local_harness.vibe._session_api_extensions import (
    AUTHORIZATION_REQUIRED,
    NOTICE,
    SUBAGENT_REGISTERED,
    TOOL_CLASSIFICATION,
    VibeExtensionEvent,
    VibeSessionReadResponse,
)
from vibe.app_server._harness_errors import harness_call
from vibe.app_server._session_api_commands import SessionApiCommands
from vibe.app_server._session_api_follower import SessionApiFeed
from vibe.app_server._session_api_projection import ApiHistoryProjection
from vibe.app_server._session_api_session import (
    ApiSessionState,
    AppliedBatch,
    final_answer,
    runtime_turn,
)
from vibe.app_server._session_api_streams import SessionApiStreams, SessionBatch
from vibe.app_server._session_backend_port import (
    SessionBackendEvent,
    SessionBackendResult,
    SessionEventSubscription,
)
from vibe.app_server._session_projection import (
    HISTORY_PAGE_WINDOW,
    ProjectedHistory,
    public_turn,
    retrying_event,
    turns_from_history,
    with_app_server_entries,
    with_callbacks_in_history,
)
from vibe.app_server._unified_history_projection import (
    with_approval_display,
    with_available_path_scopes,
)
from vibe.app_server.events import unchanged_prefix
from vibe.app_server.models import (
    PublicCallbackEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicSessionState,
    PublicTurn,
    validate_history_entry,
)
from vibe.app_server.protocol import (
    SessionCompactParams,
    SessionCompactResponse,
    SessionReadParams,
    SessionReadResponse,
)

if TYPE_CHECKING:
    from vibe.app_server._unified_harness_backend_adapter import (
        UnifiedHarnessBackendAdapter,
    )

logger = logging.getLogger(__name__)

_HARNESS_TURN: TypeAdapter[HarnessPublicTurn] = TypeAdapter(HarnessPublicTurn)


@dataclass(frozen=True, slots=True)
class ApiHistoryWindow:
    """The root's latest public history entries, and the cursor to older ones."""

    history: list[PublicHistoryEntry]
    history_before_cursor: str | None


def _history_limit(params: SessionReadParams) -> int:
    history = params.history
    return history.limit if history is not None else HISTORY_PAGE_WINDOW


class SessionApiPath:
    """The Session API side of one root Session's adapter."""

    def __init__(
        self, adapter: UnifiedHarnessBackendAdapter, commands: SessionApiCommands
    ) -> None:
        self._adapter = adapter
        self.commands = commands
        self._streams: SessionApiStreams | None = None
        self._states: dict[str, ApiSessionState] = {}
        # Cleared while a compaction reply is on its way, so the client gets
        # the reply before the compaction's updates.
        self._events_released = asyncio.Event()
        self._events_released.set()
        # Client events of batches translated before the compaction reply, by
        # Session API event ID; the stream yields them unchanged later.
        self._pretranslated: dict[str, list[SessionBackendEvent]] = {}
        # Reads waiting for their bookmark to be published, by API event ID.
        # Each gets the client event ID current when that event went out.
        self._pending_reads: dict[str, list[asyncio.Future[int]]] = {}
        # The root's last live history with its MCP app routes linked, and
        # the projection it came from: an update links only what changed.
        self._linked: tuple[ApiHistoryProjection | None, list[PublicHistoryEntry]] = (
            None,
            [],
        )
        # Whether a root question was resolved since the last live update: the
        # client then holds the resolution's copy, which the next update links.
        self._question_resolved = False

    async def read(self, params: SessionReadParams) -> SessionReadResponse:
        """Read the root Session through the Session API.

        While events are followed, the reply waits until the stream published
        the read's bookmark, so its ``event_id`` covers every update the read
        includes.
        """
        adapter = self._adapter
        await self.commands.ensure_open()
        read, extensions = await self._read(adapter.session_id, _history_limit(params))
        cursor = await self._read_cursor(read.last_event_id)
        state = ApiSessionState(adapter.session_id, adapter._session_display_roots())
        state.reset(read, extensions)
        response = self._read_response(state, read, event_id=cursor)
        return response.model_copy(
            update={"state": adapter._state_with_child_summaries(response.state)}
        )

    async def subscribe(self, params: SessionReadParams) -> SessionEventSubscription:
        """Open the root's Session API stream and its subagents' streams.

        The starting state comes from ``session/read`` and ``vibe/session/read``
        taken at the same moment; the streams follow from that read's bookmark.

        The client event IDs start from the Runtime's projection watermark, as
        on the direct path. The API's bookmarks are opaque and restart when the
        Session opens again, so they cannot keep the numbering above what a
        client saw from an earlier attachment.
        """
        adapter = self._adapter
        await self.commands.ensure_open()
        history_limit = _history_limit(params)
        await self._raise_event_floor_to_runtime_watermark()
        read, extensions = await self._read(adapter.session_id, history_limit)
        state = ApiSessionState(adapter.session_id, adapter._session_display_roots())
        state.reset(read, extensions)
        self._states = {adapter.session_id: state}
        adapter._publish_startup_notices()
        runtime = self._runtime_state(state)
        adapter._seed_tool_telemetry(runtime)
        adapter._seed_artifact_effects(runtime)
        snapshot = self._read_response(state, read)
        adapter._seed_stats(snapshot.state)
        adapter._track_context_tokens(snapshot.state)
        streams = SessionApiStreams(self.commands.transport)
        await streams.start(read.last_event_id)
        streams.mark_baseline(adapter.session_id, read.last_event_id)
        self._streams = streams
        for child_id, (child_read, child_extensions) in streams.child_reads().items():
            self._adopt_child(child_id, child_read, child_extensions)
            streams.mark_baseline(child_id, child_read.last_event_id)
        snapshot = snapshot.model_copy(
            update={"state": adapter._state_with_child_summaries(snapshot.state)}
        )
        snapshot = snapshot.model_copy(
            update={"state": await adapter._settle_goal_turns_on_attach(snapshot.state)}
        )
        adapter._translated_state = snapshot.state
        return SessionEventSubscription(
            snapshot=snapshot, events=self._translated_events(streams)
        )

    async def read_open_callbacks(self) -> list[PublicCallbackEntry]:
        """The callbacks open in the root Session and its subagents.

        ``callbacks/tree`` says which callbacks the Runtime holds open; each
        Session's state gives the details only its stream carries. A callback
        the stream of a followed Session has not brought yet is left out: the
        stream sends it to the client once it does. A subagent the stream does
        not follow yet is read for its details.
        """
        adapter = self._adapter
        await self.commands.ensure_open()
        tree = await self.commands.transport.request(
            "app_server/session/callbacks/tree",
            CallbacksTreeParams(session_id=adapter.session_id),
            CallbacksTreeResponse,
        )
        # Without a stream, no state follows the Runtime: read every Session.
        states = dict(self._states) if adapter._events_subscribed else {}
        callbacks: list[PublicCallbackEntry] = []
        for item in tree.callbacks:
            state = states.get(item.session_id)
            if state is None:
                try:
                    read, extensions = await self._read_page(
                        item.session_id, HISTORY_PAGE_WINDOW
                    )
                except Exception:
                    if item.session_id == adapter.session_id:
                        raise
                    # A subagent that stopped since the tree read has no callbacks.
                    logger.warning(
                        "Could not read subagent %s for its open callbacks",
                        item.session_id,
                        exc_info=True,
                    )
                    continue
                state = ApiSessionState(
                    item.session_id, adapter._session_display_roots()
                )
                state.reset(read, extensions)
                states[item.session_id] = state
            raw = state.open_callback_entry(item.callback)
            if raw is None:
                continue
            callback = validate_history_entry(raw)
            if isinstance(callback, PublicCallbackEntry):
                callbacks.append(
                    with_available_path_scopes(with_approval_display(callback))
                )
        return callbacks

    async def flush_events(self) -> None:
        """Wait until the stream published the last event the Session has now.

        API event IDs are opaque, so they can be matched but not ordered.
        """
        adapter = self._adapter
        streams = self._streams
        if not adapter._events_subscribed or streams is None:
            return
        _, extensions = await self.commands.transport.read_with_extensions(
            ApiSessionReadParams(session_id=adapter.session_id)
        )
        await streams.wait_observed(adapter.session_id, extensions.last_event_id)

    async def read_window(self, window: int) -> ApiHistoryWindow:
        """The root's latest ``window`` history entries, read with ``history/list``.

        ``window`` counts Session API entries, as a tool's call and result are
        two; each window ends where the latest one does, so the adapter cuts
        a client's page from it as on the direct path, whose cursors are
        public entry IDs.
        """
        adapter = self._adapter
        await self.commands.ensure_open()
        session_id = adapter.session_id
        latest = await self._history_page(
            session_id, ApiPageRequest(limit=window, direction="backward")
        )
        page = await self._with_lone_result_call(session_id, latest)
        has_older = page.next_cursor is not None
        projection = ApiHistoryProjection(session_id, adapter._session_display_roots())
        history = with_app_server_entries(
            projection.reset(page.items),
            has_older=has_older,
            goal_notices=adapter._goal_notices.notices,
            mcp_apps=adapter._mcp_apps,
        )
        return ApiHistoryWindow(
            history=history,
            history_before_cursor=history[0].id if history and has_older else None,
        )

    async def read_turns(self) -> list[PublicTurn]:
        """Every Turn of the root, oldest first, read with ``turns/list``.

        The API's Turns do not name the message that started them: as on the
        direct path, it comes from the entries of the latest history window.
        """
        adapter = self._adapter
        await self.commands.ensure_open()
        session_id = adapter.session_id
        states: list[TurnState] = []
        cursor: str | None = None
        while True:
            listed = await self.commands.transport.request(
                "app_server/session/turns/list",
                ApiTurnsListParams(
                    session_id=session_id,
                    page=ApiPageRequest(
                        limit=HISTORY_PAGE_WINDOW, direction="forward", cursor=cursor
                    ),
                ),
                ApiTurnsListResponse,
            )
            states.extend(listed.page.items)
            cursor = listed.page.next_cursor
            if cursor is None:
                break
        window = await self.read_window(HISTORY_PAGE_WINDOW)
        known = {
            turn.id: turn for turn in turns_from_history(window.history, session_id)
        }
        turns: list[PublicTurn] = []
        session_created_at: int | None = None
        for state in states:
            seen = known.get(state.id)
            started_at = state.started_at
            if started_at is None and seen is not None:
                started_at = seen.started_at
            if started_at is None:
                started_at = state.completed_at
            if started_at is None:
                # As for the latest Turn, the Session's creation stands in.
                if session_created_at is None:
                    session_created_at = await self._session_created_at(session_id)
                started_at = session_created_at
            raw = runtime_turn(state, session_id, {}, fallback_started_at=started_at)
            turn = public_turn(_HARNESS_TURN.validate_python(raw)).model_copy(
                update={"final_answer": final_answer(state)}
            )
            if seen is not None:
                turn = turn.model_copy(update={"input_entry_id": seen.input_entry_id})
            turns.append(turn)
        return turns

    async def _session_created_at(self, session_id: str) -> int:
        read = await self.commands.transport.request(
            "app_server/session/read",
            ApiSessionReadParams(session_id=session_id),
            ApiSessionReadResponse,
        )
        return read.state.session.created_at

    async def read_plugin_info(self) -> ApiPluginInfo:
        """What the root Session bound, read with ``plugin/info``."""
        await self.commands.ensure_open()
        response = await self.commands.transport.request(
            "app_server/session/plugin/info",
            ApiPluginInfoParams(session_id=self._adapter.session_id),
            ApiPluginInfoResponse,
        )
        return response.info

    async def compact(
        self, params: SessionCompactParams
    ) -> SessionBackendResult[SessionCompactResponse]:
        """Compact through the Session API, replying before the compaction's updates.

        The API compacts and publishes in one step. The stream is held from
        before the request until the reply is out, and the reply's state is
        the compacted one, so clients apply the updates after it.
        """
        adapter = self._adapter
        adapter._hold_events()
        self._events_released.clear()
        streams = self._streams
        # Only a followed stream's state moves with the compaction's updates.
        followed = streams is not None and adapter._events_subscribed
        try:
            before = await self._read(adapter.session_id, 1)
            compacted_reply = await harness_call(
                self.commands.compact(params.extra_instructions or None)
            )
            if streams is not None and followed:
                await self._pretranslate_batches(
                    streams, before[0].last_event_id, compacted_reply.last_event_id
                )
        except BaseException:
            self._pretranslated.clear()
            adapter._defer_event_flush = False
            self._events_released.set()
            raise
        state = adapter._translated_state if followed else None
        if state is None:
            read, extensions = await self._read(adapter.session_id, HISTORY_PAGE_WINDOW)
            compacted = ApiSessionState(
                adapter.session_id, adapter._session_display_roots()
            )
            compacted.reset(read, extensions)
            state = self._read_response(compacted, read).state
        after_response, on_response_abandoned = adapter._release_events_after_reply(
            None, None, on_release=self._events_released.set
        )
        return SessionBackendResult(
            response=SessionCompactResponse(
                summary=compacted_reply.summary or "",
                state=state,
                session_log=await adapter._session_log_summary(),
            ),
            after_response=after_response,
            on_response_abandoned=on_response_abandoned,
        )

    async def reset_after_rewind(self) -> None:
        """Read the root again: no Session API event removes entries.

        The Session and its latest Turn are taken from the same read as the
        history. The rewind reply carries them, so later updates are patches
        against what the client holds.
        """
        adapter = self._adapter
        state = self._states.get(adapter.session_id)
        if state is None:
            return
        read, extensions = await self._read(adapter.session_id, HISTORY_PAGE_WINDOW)
        state.reset(read, extensions)
        previous = adapter._translated_state
        if previous is not None:
            reread = self._read_response(state, read).state
            adapter._translated_state = previous.model_copy(
                update={
                    "session": reread.session,
                    "history": reread.history,
                    "turns": reread.turns,
                }
            )

    def follower_feed(self) -> SessionApiFeed:
        """The Session follower's own stream of the root Session."""
        adapter = self._adapter
        return SessionApiFeed(
            self.commands,
            display_roots=adapter._session_display_roots,
            public_turn=public_turn,
        )

    def release_events(self) -> None:
        """Let the stream go on, as the adapter shuts down."""
        self._events_released.set()

    def forget_child(self, child_id: str) -> None:
        """Drop a stopped subagent's state: the Harness has unloaded it."""
        self._states.pop(child_id, None)

    async def _pretranslate_batches(
        self, streams: SessionApiStreams, after_event_id: str, last_event_id: str
    ) -> None:
        """Translate the root's batches up to ``last_event_id`` before a reply.

        The reply then carries the state those batches lead to, under the
        client event ID they end on. The live stream yields the same events
        later, unchanged, so the client sees each one once.
        """
        if after_event_id == last_event_id:
            return
        session_id = self._adapter.session_id
        reader = self.commands.transport.read_stream(
            EventsReadParams(session_id=session_id, after_event_id=after_event_id)
        )
        try:
            async for batch in reader:
                session_batch = SessionBatch(session_id, batch)
                events = [
                    event async for event in self._batch_events(streams, session_batch)
                ]
                self._pretranslated[batch.events[-1].event_id] = events
                if any(item.event_id == last_event_id for item in batch.events):
                    return
        finally:
            await cast(Any, reader).aclose()

    async def _raise_event_floor_to_runtime_watermark(self) -> None:
        """Keep the client event IDs at or above the Runtime's watermark.

        A direct Runtime read: the Session API carries no counter that
        survives a reopen.
        """
        adapter = self._adapter
        runtime = await adapter._session.read(
            HarnessSessionReadParams(session_id=adapter.session_id, history_limit=0)
        )
        adapter._event_id = max(adapter._event_id, runtime.snapshot.watermark)

    async def _read(
        self, session_id: str, history_limit: int
    ) -> tuple[ApiSessionReadResponse, VibeSessionReadResponse]:
        """A read of the latest ``history_limit`` entries, without splitting a tool.

        A tool is a call and a result, next to each other
        (``_with_lone_result_call``).
        """
        read, extensions = await self._read_page(session_id, history_limit)
        history = read.state.history
        if history is None:
            return read, extensions
        widened = await self._with_lone_result_call(session_id, history)
        if widened is history:
            return read, extensions
        state = read.state.model_copy(update={"history": widened})
        return read.model_copy(update={"state": state}), extensions

    async def _with_lone_result_call(
        self, session_id: str, history: Page[HistoryEntry]
    ) -> Page[HistoryEntry]:
        """``history``, with the call of a result at its older edge added.

        A page that ends at a result keeps it only with its call, so the entry
        just before the page edge joins it. The page cursor stays valid while
        entries are appended, so that entry is the call however the Session
        moved on.
        """
        if not _starts_with_a_lone_result(history):
            return history
        older = await self._history_page(
            session_id,
            ApiPageRequest(limit=1, direction="backward", cursor=history.next_cursor),
        )
        return history.model_copy(
            update={
                "items": [*history.items, *older.items],
                "next_cursor": older.next_cursor,
            }
        )

    async def _history_page(
        self, session_id: str, page: ApiPageRequest
    ) -> Page[HistoryEntry]:
        listed = await self.commands.transport.request(
            "app_server/session/history/list",
            SessionHistoryListParams(session_id=session_id, page=page),
            SessionHistoryListResponse,
        )
        return listed.page

    async def _read_page(
        self, session_id: str, history_limit: int
    ) -> tuple[ApiSessionReadResponse, VibeSessionReadResponse]:
        return await self.commands.transport.read_with_extensions(
            ApiSessionReadParams(
                session_id=session_id,
                history=ApiPageRequest(limit=history_limit, direction="backward"),
                turns=ApiPageRequest(limit=1, direction="backward"),
            )
        )

    def _runtime_state(self, state: ApiSessionState) -> HarnessPublicSessionState:
        """The Runtime-format state of the page, for the telemetry helpers."""
        runtime = state.runtime_state()
        return runtime.model_copy(
            update={
                "history": runtime.history.model_copy(
                    update={"entries": state.history_projection.raw_entries}
                )
            }
        )

    def _read_response(
        self,
        state: ApiSessionState,
        read: ApiSessionReadResponse | None = None,
        *,
        event_id: int | None = None,
        has_older: bool = False,
        live_from: PublicSessionState | None = None,
    ) -> SessionReadResponse:
        """Vibe's read response for one Session API state.

        A read carries open callbacks as history entries, as a resync must.
        A live update from the state the client holds (``live_from``) leaves
        them out: the callback's own events add it. It also links MCP app
        routes only to the entries that changed (see ``_linked_history``).
        """
        has_older = has_older or (
            read is not None
            and read.state.history is not None
            and read.state.history.next_cursor is not None
        )
        response = self._adapter._root_read_response(
            HarnessSessionSnapshot(
                state=state.runtime_state(), history_limit=0, watermark=0
            ),
            event_id=event_id,
            projected_history=(
                ProjectedHistory(
                    entries=self._linked_history(state, live_from),
                    has_older=has_older,
                    apps_linked=True,
                )
                if live_from is not None
                else ProjectedHistory(entries=state.history, has_older=has_older)
            ),
        )
        current = _with_final_answers(response.state, state)
        if live_from is None:
            current = with_callbacks_in_history(current)
        return response.model_copy(update={"state": current})

    def _linked_history(
        self, state: ApiSessionState, client_state: PublicSessionState
    ) -> list[PublicHistoryEntry]:
        """The root's history with its MCP app routes linked, for a live update.

        An entry links the same route each time (``SessionMcpApps`` keeps the
        first lookup), so the entries no event replaced since the last update
        keep their linked copies. An entry the client holds as a settled
        callback takes the client's copy here too, as
        ``_with_settled_callbacks_kept`` does afterwards, so that later updates
        still share it with the client: a changed entry, or any entry once a
        callback was resolved since the last update.
        """
        projection = state.history_projection
        history, unchanged = projection.take_history()
        source, linked = self._linked
        start = unchanged if source is projection else 0
        linked = [*linked[:start], *self._adapter._mcp_apps.apply(history[start:])]
        # A resolution sent since the last update can name any entry; otherwise
        # only the changed entries can need the client's copy.
        if self._question_resolved:
            start = 0
            self._question_resolved = False
        if any(_is_settled_callback(entry) for entry in linked[start:]):
            settled = {
                entry.id: entry
                for entry in client_state.history or []
                if _is_settled_callback(entry)
            }
            linked[start:] = [settled.get(entry.id, entry) for entry in linked[start:]]
        self._linked = (projection, linked)
        return list(linked)

    def _adopt_child(
        self,
        child_id: str,
        read: ApiSessionReadResponse,
        extensions: VibeSessionReadResponse,
    ) -> None:
        adapter = self._adapter
        state = ApiSessionState(child_id, adapter._session_display_roots())
        state.reset(read, extensions)
        self._states[child_id] = state
        adapter._child_states[child_id] = self._child_public_state(state)

    def _child_public_state(self, state: ApiSessionState) -> PublicSessionState:
        return self._adapter._child_read_state(
            state.session_id,
            HarnessSessionSnapshot(
                state=state.runtime_state(), history_limit=0, watermark=0
            ),
            projected_history=ProjectedHistory(entries=state.history, has_older=False),
        )

    async def _translated_events(
        self, streams: SessionApiStreams
    ) -> AsyncIterator[SessionBackendEvent]:
        """The client's events, from the Session API streams.

        Each batch holds every item of one Runtime update and is applied to
        that Session's state as a whole before its events are published.
        """
        adapter = self._adapter
        await adapter._mark_events_subscribed()
        try:
            async for item in adapter._with_host_events(
                _batches_as_events(streams, self._events_released)
            ):
                if isinstance(item, SessionBackendEvent):
                    yield item
                    await adapter._settle_host_event()
                    continue
                session_batch = cast(SessionBatch, item["batch"])
                pretranslated = self._pretranslated.pop(
                    session_batch.batch.events[-1].event_id, None
                )
                if pretranslated is not None:
                    for backend_event in pretranslated:
                        yield backend_event
                else:
                    async for backend_event in self._batch_events(
                        streams, session_batch
                    ):
                        yield backend_event
                event_ids = [event.event_id for event in session_batch.batch.events]
                streams.observed(session_batch.session_id, event_ids)
                if session_batch.session_id == adapter.session_id:
                    self._resolve_reads(event_ids)
        finally:
            await streams.close()
            self._resolve_reads(list(self._pending_reads))
            await adapter._finish_event_stream()

    def _resolve_reads(self, event_ids: Sequence[str]) -> None:
        for event_id in event_ids:
            for waiter in self._pending_reads.pop(event_id, ()):
                if not waiter.done():
                    waiter.set_result(self._adapter._event_id)

    async def _read_cursor(self, event_id: str) -> int:
        """The client event ID that covers the API event ``event_id``.

        It is taken when the stream publishes that event, not later, so a read
        never claims updates it does not include. An event already published
        takes the current ID, as the Runtime path does.
        """
        adapter = self._adapter
        streams = self._streams
        if (
            not adapter._events_subscribed
            or streams is None
            or streams.is_observed(adapter.session_id, event_id)
        ):
            return adapter._event_id
        waiter: asyncio.Future[int] = asyncio.get_running_loop().create_future()
        self._pending_reads.setdefault(event_id, []).append(waiter)
        return await waiter

    async def _batch_events(
        self, streams: SessionApiStreams, session_batch: SessionBatch
    ) -> AsyncIterator[SessionBackendEvent]:
        adapter = self._adapter
        state = self._states.get(session_batch.session_id)
        if state is None:
            return
        applied = state.apply(session_batch.batch)
        root = session_batch.session_id == adapter.session_id
        previous = adapter._translated_state
        if previous is None:
            raise RuntimeError("Unified event stream has no translated state")
        if root:
            translated, current = await self._root_update(state, applied, previous)
            # Only a question's resolution joins the client's history.
            self._question_resolved |= any(
                cast(Mapping[str, Any], callback["detail"]).get("kind") == "user_input"
                for callback in applied.resolved_callbacks
            )
        else:
            translated, current = await self._child_update(state, previous)
        adapter._translated_state = current
        if root and applied.turn_terminated:
            adapter._settle_configuration_at_turn_end()
        async for backend_event in adapter._with_turn_start_effects(translated):
            yield backend_event
        for kind, callbacks in (
            ("callback_requested", applied.opened_callbacks),
            ("callback_resolved", applied.resolved_callbacks),
        ):
            for callback in callbacks:
                for backend_event in (
                    adapter._callback_events({"type": kind, "callback": callback}) or []
                ):
                    yield backend_event
        for signal in applied.signals:
            for backend_event in await self._signal_events(
                streams, session_batch.session_id, signal
            ):
                yield backend_event

    async def _root_update(
        self,
        state: ApiSessionState,
        applied: AppliedBatch,
        previous: PublicSessionState,
    ) -> tuple[list[SessionBackendEvent], PublicSessionState]:
        adapter = self._adapter
        runtime = state.runtime_state()
        changed = runtime.model_copy(
            update={
                "history": runtime.history.model_copy(
                    update={
                        "entries": [
                            change.raw
                            for change in applied.history_changes
                            if change.raw is not None
                        ]
                    }
                )
            }
        )
        adapter._record_root_update(changed)
        with adapter._translating_goal_update():
            await adapter._drain_pending_shell_effects()
            # A live update keeps the window it extends, older entries included.
            current = self._read_response(
                state,
                has_older=previous.history_before_cursor is not None,
                live_from=previous,
            ).state
            adapter._track_context_tokens(current)
            current = current.model_copy(
                update={"history_before_cursor": previous.history_before_cursor}
            )
            adapter._rewind_snapshots.observe(current)
            current = await adapter._record_unseen_transition(previous, current)
            current = await adapter._observe_goal_turns(previous, current)
        current = _with_settled_callbacks_kept(previous, current)
        return adapter._translate_snapshot_update(previous, current)

    async def _child_update(
        self, state: ApiSessionState, root_state: PublicSessionState
    ) -> tuple[list[SessionBackendEvent], PublicSessionState]:
        adapter = self._adapter
        previous = adapter._child_states.get(state.session_id)
        current = self._child_public_state(state)
        child_events: list[SessionBackendEvent] = []
        if previous is not None:
            child_events, current = adapter._child_state_events(previous, current)
        adapter._child_states[state.session_id] = current
        refreshed = adapter._state_with_child_summaries(root_state)
        summary_events, root_state = adapter._child_summary_events(
            root_state, refreshed
        )
        translated = [*child_events, *summary_events]
        if (
            previous is not None
            and previous.retrying != current.retrying
            and current.retrying is not None
        ):
            translated.append(retrying_event(state.session_id, current.retrying))
        goal_events, root_state = await adapter._child_goal_progress(root_state)
        return [*translated, *goal_events], root_state

    async def _signal_events(
        self, streams: SessionApiStreams, session_id: str, signal: VibeExtensionEvent
    ) -> list[SessionBackendEvent]:
        """Client events for a ``vibe.*`` item that carries no state."""
        adapter = self._adapter
        root = session_id == adapter.session_id
        data = cast(Mapping[str, object], signal.data)
        events: list[SessionBackendEvent] = []
        if signal.type == NOTICE and root:
            events.append(adapter._notice_event({**data, "type": "notice"}))
        elif signal.type == AUTHORIZATION_REQUIRED:
            authorization = await adapter._authorization_event(
                data, session_id=session_id
            )
            if authorization is not None:
                events.append(authorization)
        elif signal.type == TOOL_CLASSIFICATION:
            adapter._emit_classification_telemetry(data)
        elif signal.type == SUBAGENT_REGISTERED and root:
            child_id = data.get("sessionId")
            if isinstance(child_id, str):
                events.extend(await self._subagent_registered(streams, child_id))
        return events

    async def _subagent_registered(
        self, streams: SessionApiStreams, child_id: str
    ) -> list[SessionBackendEvent]:
        """Follow a subagent the root just started.

        Its stream starts after the read, so the callbacks the read finds open
        are sent from the read.
        """
        adapter = self._adapter
        child_read = await streams.add_child(child_id)
        previous = adapter._translated_state
        if child_read is None or previous is None:
            return []
        self._adopt_child(child_id, *child_read)
        streams.mark_baseline(child_id, child_read[0].last_event_id)
        current = adapter._state_with_child_summaries(previous)
        translated, current = adapter._child_summary_events(previous, current)
        adapter._translated_state = current
        for callback in self._states[child_id].open_callback_entries():
            translated.extend(
                adapter._callback_events({
                    "type": "callback_requested",
                    "callback": callback,
                })
                or []
            )
        return translated


def _starts_with_a_lone_result(history: Page[HistoryEntry]) -> bool:
    """Whether the page's oldest entry is a tool result read without its call."""
    if not history.items or history.next_cursor is None:
        return False
    oldest = min(history.items, key=lambda entry: entry.index)
    return isinstance(oldest, ToolResultHistoryEntry)


def _with_settled_callbacks_kept(
    previous: PublicSessionState, current: PublicSessionState
) -> PublicSessionState:
    """``current``, with each callback the client already has as settled kept as sent.

    The Runtime keeps a settled question in history, so it arrives again as a
    history entry after its resolution was sent. The resolution's copy was
    rebuilt from the API callback and differs in its opening time; a client
    refuses any change to a completed entry, so the copy it has stays.
    """
    previous_history = previous.history or []
    current_history = current.history or []
    # The shared prefix is already the client's own copies.
    start = unchanged_prefix(previous_history, current_history)
    settled = {
        entry.id: entry
        for entry in previous_history[start:]
        if _is_settled_callback(entry)
    }
    if not settled or not current_history:
        return current
    history = [
        *current_history[:start],
        *(settled.get(entry.id, entry) for entry in current_history[start:]),
    ]
    return current.model_copy(update={"history": history})


def _with_final_answers(
    current: PublicSessionState, state: ApiSessionState
) -> PublicSessionState:
    """``current``, with each Turn's final answer taken from the Session API.

    Vibe's Turn is built in the Runtime's format, which has no answer: the
    answer comes from the API's termination, which the local runtime fills only
    with stated reasons.
    """
    if not current.turns:
        return current
    turns = [
        turn.model_copy(update={"final_answer": answer})
        if (answer := state.final_answer_of(turn.id)) is not None
        else turn
        for turn in current.turns
    ]
    return current.model_copy(update={"turns": turns})


def _is_settled_callback(entry: PublicHistoryEntry) -> bool:
    return (
        isinstance(entry, PublicCallbackEntry)
        and entry.generation_status is PublicEntryGenerationStatus.COMPLETED
    )


async def _batches_as_events(
    streams: SessionApiStreams, released: asyncio.Event
) -> AsyncIterator[dict[str, Any]]:
    """The streams' batches, wrapped so the host-event merge can carry them.

    A batch waits for ``released`` here, ahead of the merge: while compaction
    holds its updates, the Host's own events still go out, and a request
    waiting for them does not wait for the reply that releases the batch.
    """
    async for session_batch in streams:
        await released.wait()
        yield {"batch": session_batch}


__all__ = ["SessionApiPath"]
