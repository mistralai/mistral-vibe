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
from typing import TYPE_CHECKING, Any, cast

from mistralai_vibe_local_harness.session_api.app_server.session.models import (
    HistoryEntry,
    Page,
    PageRequest as ApiPageRequest,
    ToolResultHistoryEntry,
)
from mistralai_vibe_local_harness.session_api.app_server.session.procedures import (
    EventsReadParams,
    SessionHistoryListParams,
    SessionHistoryListResponse,
    SessionReadParams as ApiSessionReadParams,
    SessionReadResponse as ApiSessionReadResponse,
)
from mistralai_vibe_local_harness.session_protocol import (
    PublicSessionState as HarnessPublicSessionState,
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
from vibe.app_server._session_api_commands import SessionApiCommands
from vibe.app_server._session_api_session import ApiSessionState, AppliedBatch
from vibe.app_server._session_api_streams import SessionApiStreams, SessionBatch
from vibe.app_server._session_backend_port import (
    SessionBackendEvent,
    SessionBackendResult,
    SessionEventSubscription,
)
from vibe.app_server._unified_harness_backend_adapter import (
    _HISTORY_PAGE_WINDOW,
    _harness_call,
    _history_limit,
    _ProjectedHistory,
    _retrying_event,
    _with_callbacks_in_history,
)
from vibe.app_server.models import (
    PublicCallbackEntry,
    PublicEntryGenerationStatus,
    PublicSessionState,
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
        adapter._translated_state = snapshot.state
        return SessionEventSubscription(
            snapshot=snapshot, events=self._translated_events(streams)
        )

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
            compacted_reply = await _harness_call(
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
            read, extensions = await self._read(
                adapter.session_id, _HISTORY_PAGE_WINDOW
            )
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
        read, extensions = await self._read(adapter.session_id, _HISTORY_PAGE_WINDOW)
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

        A tool is a call and a result, next to each other. A page that ends at
        a result keeps it only with its call, so the read adds the entry just
        before its page edge. The page cursor stays valid while entries are
        appended, so that entry is the call however the Session moved on.
        """
        read, extensions = await self._read_page(session_id, history_limit)
        history = read.state.history
        if history is None or not _starts_with_a_lone_result(history):
            return read, extensions
        older = await self.commands.transport.request(
            "app_server/session/history/list",
            SessionHistoryListParams(
                session_id=session_id,
                page=ApiPageRequest(
                    limit=1, direction="backward", cursor=history.next_cursor
                ),
            ),
            SessionHistoryListResponse,
        )
        widened = history.model_copy(
            update={
                "items": [*history.items, *older.page.items],
                "next_cursor": older.page.next_cursor,
            }
        )
        state = read.state.model_copy(update={"history": widened})
        return read.model_copy(update={"state": state}), extensions

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
        callbacks_in_history: bool = True,
        event_id: int | None = None,
    ) -> SessionReadResponse:
        """Vibe's read response for one Session API state.

        A read carries open callbacks as history entries, as a resync must.
        The live update leaves them out: the callback's own events add it.
        """
        has_older = (
            read is not None
            and read.state.history is not None
            and read.state.history.next_cursor is not None
        )
        response = self._adapter._root_read_response(
            HarnessSessionSnapshot(
                state=state.runtime_state(), history_limit=0, watermark=0
            ),
            event_id=event_id,
            projected_history=_ProjectedHistory(
                entries=state.history, has_older=has_older
            ),
        )
        current = response.state
        if callbacks_in_history:
            current = _with_callbacks_in_history(current)
        return response.model_copy(update={"state": current})

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
            projected_history=_ProjectedHistory(entries=state.history, has_older=False),
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
            async for item in adapter._with_host_events(_batches_as_events(streams)):
                if isinstance(item, SessionBackendEvent):
                    yield item
                    await adapter._settle_host_event()
                    continue
                session_batch = cast(SessionBatch, item["batch"])
                await self._events_released.wait()
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
        else:
            translated, current = self._child_update(state, previous)
        adapter._translated_state = current
        for backend_event in translated:
            yield backend_event
            await adapter._bump_promoted_queued_turn(backend_event.event)
        for kind, callbacks in (
            ("callback_requested", applied.opened_callbacks),
            ("callback_resolved", applied.resolved_callbacks),
        ):
            for callback in callbacks:
                for backend_event in (
                    adapter._callback_events(
                        {"type": kind, "callback": callback}, include_history=root
                    )
                    or []
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
        await adapter._drain_pending_shell_effects()
        current = self._read_response(state, callbacks_in_history=False).state
        adapter._track_context_tokens(current)
        current = current.model_copy(
            update={"history_before_cursor": previous.history_before_cursor}
        )
        adapter._rewind_snapshots.observe(current)
        current = await adapter._record_unseen_transition(previous, current)
        current = _with_settled_callbacks_kept(previous, current)
        return adapter._translate_snapshot_update(previous, current)

    def _child_update(
        self, state: ApiSessionState, root_state: PublicSessionState
    ) -> tuple[list[SessionBackendEvent], PublicSessionState]:
        adapter = self._adapter
        previous = adapter._child_states.get(state.session_id)
        current = self._child_public_state(state)
        adapter._child_states[state.session_id] = current
        refreshed = adapter._state_with_child_summaries(root_state)
        translated, root_state = adapter._child_summary_events(root_state, refreshed)
        if (
            previous is not None
            and previous.retrying != current.retrying
            and current.retrying is not None
        ):
            translated.append(_retrying_event(state.session_id, current.retrying))
        return translated, root_state

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
        """Follow a subagent the root just started."""
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
    settled = {
        entry.id: entry
        for entry in previous.history or []
        if isinstance(entry, PublicCallbackEntry)
        and entry.generation_status is PublicEntryGenerationStatus.COMPLETED
    }
    if not settled or not current.history:
        return current
    history = [settled.get(entry.id, entry) for entry in current.history]
    return current.model_copy(update={"history": history})


async def _batches_as_events(
    streams: SessionApiStreams,
) -> AsyncIterator[dict[str, Any]]:
    """The streams' batches, wrapped so the host-event merge can carry them."""
    async for session_batch in streams:
        yield {"batch": session_batch}


__all__ = ["SessionApiPath"]
