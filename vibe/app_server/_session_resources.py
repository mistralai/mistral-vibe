from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import Literal
from uuid import uuid4

from vibe.app_server._model import validate_wire
from vibe.app_server._streaming import BoundedEventQueue, stream_request
from vibe.app_server.client_state import ClientSessionState
from vibe.app_server.connection import AppServerResourceConnection
from vibe.app_server.events import (
    AppServerEvent,
    ClientProjection,
    HistoryEntryAdded,
    HistoryEntryUpdated,
)
from vibe.app_server.models import (
    ContentBlock,
    PreparedPrompt,
    PublicHistoryEntry,
    PublicHistoryPage,
    PublicSessionState,
    SavedSessionSummary,
    ScheduledLoop,
    SessionLogSummary,
    WorkspaceTrustDecision,
)
from vibe.app_server.protocol import (
    EmptyResponse,
    HistoryListParams,
    HistoryListResponse,
    LoopsClearParams,
    LoopsClearResponse,
    LoopsCreateParams,
    LoopsCreateResponse,
    LoopsDeleteParams,
    LoopsDeleteResponse,
    LoopsListParams,
    LoopsListResponse,
    ReviewBaselineParams,
    ReviewBaselineResponse,
    ReviewHunksParams,
    ReviewHunksResponse,
    ReviewMutationParams,
    ReviewStateParams,
    ReviewStateResponse,
    ReviewTurnDiffParams,
    ReviewTurnDiffResponse,
    SessionCompactParams,
    SessionCompactResponse,
    SessionDeleteParams,
    SessionForkParams,
    SessionForkResponse,
    SessionHistoryClearParams,
    SessionHistoryClearResponse,
    SessionListParams,
    SessionListResponse,
    SessionLogReadParams,
    SessionLogReadResponse,
    SessionResumeParams,
    SessionResumeResponse,
    SessionRewindParams,
    SessionRewindReadParams,
    SessionRewindReadResponse,
    SessionRewindResponse,
    SessionSettingsUpdateParams,
    SessionTitleUpdateParams,
    SessionTitleUpdateResponse,
    ShellInterruptParams,
    ShellInterruptResponse,
    ShellRunParams,
    ShellRunResponse,
    WorkspacePromptPrepareParams,
    WorkspacePromptPrepareResponse,
    WorkspaceTrustDecisionParams,
    WorkspaceTrustStatusParams,
    WorkspaceTrustStatusResponse,
)
from vibe.app_server.review import ReviewOwner, ReviewTarget

type ShellTimelineEvent = HistoryEntryAdded | HistoryEntryUpdated
type ShellEvent = ShellTimelineEvent | ShellRunResponse


class ShellResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state
        self._events: dict[str, asyncio.Queue[ShellTimelineEvent]] = {}

    async def run(
        self, command: str, *, timeout_seconds: float = 30.0
    ) -> AsyncGenerator[ShellEvent, None]:
        client = await self._connection.connect()
        operation_id = str(uuid4())
        events = BoundedEventQueue[ShellTimelineEvent]()
        self._events[operation_id] = events
        completed = False
        try:
            async for event in stream_request(
                client,
                "shell/run",
                ShellRunParams(
                    session_id=self._state.session_id,
                    operation_id=operation_id,
                    command=command,
                    timeout_seconds=timeout_seconds,
                ),
                events,
                ShellRunResponse,
            ):
                if isinstance(event, ShellRunResponse):
                    completed = True
                yield event
        finally:
            try:
                if not completed:
                    with suppress(Exception):
                        validate_wire(
                            ShellInterruptResponse,
                            await client.request(
                                "shell/interrupt",
                                ShellInterruptParams(
                                    session_id=self._state.session_id,
                                    operation_id=operation_id,
                                ),
                            ),
                        )
            finally:
                self._events.pop(operation_id, None)

    async def consume_event(self, event: AppServerEvent) -> bool:
        match event:
            case HistoryEntryAdded(entry=entry) | HistoryEntryUpdated(entry=entry):
                events = self._events.get(entry.id)
            case _:
                return False
        if events is None:
            return False
        await events.put(event)
        return True


class SessionResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state

    @property
    def id(self) -> str:
        return self._state.session_id

    @property
    def state(self) -> PublicSessionState:
        return self._state.state

    @property
    def history(self) -> list[PublicHistoryEntry]:
        return self._state.projection.history

    async def list(self, cwd: str | None = None) -> list[SavedSessionSummary]:
        client = await self._connection.connect()
        response = validate_wire(
            SessionListResponse,
            await client.request("session/list", SessionListParams(cwd=cwd)),
        )
        return response.sessions

    async def delete(self, session_id: str) -> None:
        client = await self._connection.connect()
        validate_wire(
            EmptyResponse,
            await client.request(
                "session/delete", SessionDeleteParams(session_id=session_id)
            ),
        )

    async def rename(self, title: str) -> str:
        return (await self.rename_with_metadata(title)).title

    async def rename_with_metadata(self, title: str) -> SessionTitleUpdateResponse:
        client = await self._connection.connect()
        response = validate_wire(
            SessionTitleUpdateResponse,
            await client.request(
                "session/title/update",
                SessionTitleUpdateParams(
                    session_id=self._state.session_id, title=title
                ),
            ),
        )
        self._state.session_log = self._state.session_log.model_copy(
            update={"title": response.title}
        )
        self._state.state.session.title = response.title
        return response

    async def read_log(self) -> SessionLogSummary:
        client = await self._connection.connect()
        response = validate_wire(
            SessionLogReadResponse,
            await client.request(
                "session/log/read",
                SessionLogReadParams(session_id=self._state.session_id),
            ),
        )
        self._state.session_log = response.log
        return response.log

    async def resume(self, session_id: str) -> None:
        client = await self._connection.connect()
        response = validate_wire(
            SessionResumeResponse,
            await client.request(
                "session/resume", SessionResumeParams(session_id=session_id)
            ),
        )
        self._state.projection = ClientProjection(response.state)
        self._state.reset_usage_baseline()
        self._connection.mark_session_attached()

    async def update_settings(
        self, *, max_turns: int | None = None, max_tokens: int | None = None
    ) -> None:
        client = await self._connection.connect()
        validate_wire(
            EmptyResponse,
            await client.request(
                "session/settings/update",
                SessionSettingsUpdateParams(
                    session_id=self._state.session_id,
                    max_turns=max_turns,
                    max_tokens=max_tokens,
                ),
            ),
        )

    async def fork(
        self, entry_id: str | None = None, *, attach: bool = True
    ) -> SessionForkResponse:
        client = await self._connection.connect()
        response = validate_wire(
            SessionForkResponse,
            await client.request(
                "session/fork",
                SessionForkParams(
                    source_session_id=self._state.session_id,
                    entry_id=entry_id,
                    attach=attach,
                ),
            ),
        )
        if attach:
            self._state.projection.replace_state(response.state)
            self._state.reset_usage_baseline()
            self._connection.mark_session_attached()
        return response

    async def load_before(self, entry_id: str, limit: int = 10) -> PublicHistoryPage:
        client = await self._connection.connect()
        response = validate_wire(
            HistoryListResponse,
            await client.request(
                "history/list",
                HistoryListParams(
                    session_id=self._state.session_id, before=entry_id, limit=limit
                ),
            ),
        )
        self._state.projection.prepend_history_page(response.history)
        return response.history

    async def list_history(
        self,
        *,
        turn_id: str | None = None,
        before: str | None = None,
        after: str | None = None,
        limit: int = 200,
    ) -> PublicHistoryPage:
        client = await self._connection.connect()
        response = validate_wire(
            HistoryListResponse,
            await client.request(
                "history/list",
                HistoryListParams(
                    session_id=self._state.session_id,
                    turn_id=turn_id,
                    before=before,
                    after=after,
                    limit=limit,
                ),
            ),
        )
        return response.history

    async def clear_history(self) -> None:
        client = await self._connection.connect()
        response = validate_wire(
            SessionHistoryClearResponse,
            await client.request(
                "session/history/clear",
                SessionHistoryClearParams(session_id=self._state.session_id),
            ),
        )
        self._state.projection.replace_state(response.state)
        self._state.reset_usage_baseline()
        self._state.session_log = response.session_log
        self._connection.mark_session_attached()

    async def compact(self, extra_instructions: str = "") -> str:
        client = await self._connection.connect()
        response = validate_wire(
            SessionCompactResponse,
            await client.request(
                "session/compact/start",
                SessionCompactParams(
                    session_id=self._state.session_id,
                    extra_instructions=extra_instructions,
                ),
            ),
        )
        self._state.projection.replace_state(response.state)
        self._state.session_log = response.session_log
        self._connection.mark_session_attached()
        return response.summary

    async def rewind_has_file_changes(self, entry_id: str) -> bool:
        return bool(await self.rewind_preview(entry_id))

    async def rewind_preview(self, entry_id: str) -> list[str]:
        client = await self._connection.connect()
        response = validate_wire(
            SessionRewindReadResponse,
            await client.request(
                "session/rewind/read",
                SessionRewindReadParams(
                    session_id=self._state.session_id, entry_id=entry_id
                ),
            ),
        )
        return response.paths

    async def rewind(
        self, entry_id: str, *, restore_files: bool, inplace: bool = False
    ) -> SessionRewindResponse:
        client = await self._connection.connect()
        response = validate_wire(
            SessionRewindResponse,
            await client.request(
                "session/rewind",
                SessionRewindParams(
                    session_id=self._state.session_id,
                    entry_id=entry_id,
                    restore_files=restore_files,
                    inplace=inplace,
                ),
            ),
        )
        self._state.projection.replace_state(response.state)
        self._state.session_log = response.session_log
        self._connection.mark_session_attached()
        return response


class ReviewResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state

    async def state(self) -> ReviewStateResponse:
        client = await self._connection.connect()
        return validate_wire(
            ReviewStateResponse,
            await client.request(
                "review/state", ReviewStateParams(session_id=self._state.session_id)
            ),
        )

    async def baseline(self, path: str) -> ReviewBaselineResponse:
        client = await self._connection.connect()
        return validate_wire(
            ReviewBaselineResponse,
            await client.request(
                "review/baseline",
                ReviewBaselineParams(session_id=self._state.session_id, path=path),
            ),
        )

    async def turn_diff(self, path: str, owner: ReviewOwner) -> ReviewTurnDiffResponse:
        client = await self._connection.connect()
        return validate_wire(
            ReviewTurnDiffResponse,
            await client.request(
                "review/turnDiff",
                ReviewTurnDiffParams(
                    session_id=self._state.session_id, path=path, owner=owner
                ),
            ),
        )

    async def hunks(
        self, path: str, owner: ReviewOwner | None = None
    ) -> ReviewHunksResponse:
        client = await self._connection.connect()
        return validate_wire(
            ReviewHunksResponse,
            await client.request(
                "review/hunks",
                ReviewHunksParams(
                    session_id=self._state.session_id, path=path, owner=owner
                ),
            ),
        )

    async def approve(self, target: ReviewTarget) -> None:
        await self._mutate("review/approve", target)

    async def revert(self, target: ReviewTarget) -> None:
        await self._mutate("review/revert", target)

    async def _mutate(
        self, method: Literal["review/approve", "review/revert"], target: ReviewTarget
    ) -> None:
        client = await self._connection.connect()
        validate_wire(
            EmptyResponse,
            await client.request(
                method,
                ReviewMutationParams(session_id=self._state.session_id, target=target),
            ),
        )


class WorkspaceResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state

    async def prepare_prompt(
        self, message: str, *, title_content: list[ContentBlock] | None = None
    ) -> PreparedPrompt:
        client = await self._connection.connect()
        response = validate_wire(
            WorkspacePromptPrepareResponse,
            await client.request(
                "workspace/prompt/prepare",
                WorkspacePromptPrepareParams(
                    session_id=self._state.session_id,
                    message=message,
                    title_content=title_content,
                ),
            ),
        )
        return response.prompt

    async def trust_status(
        self, cwd: str | None = None
    ) -> WorkspaceTrustStatusResponse:
        client = await self._connection.connect()
        return validate_wire(
            WorkspaceTrustStatusResponse,
            await client.request(
                "workspace/trust/status",
                WorkspaceTrustStatusParams(cwd=cwd or self._state.state.session.cwd),
            ),
        )

    async def decide_trust(
        self, decision: WorkspaceTrustDecision, *, cwd: str | None = None
    ) -> WorkspaceTrustStatusResponse:
        client = await self._connection.connect()
        return validate_wire(
            WorkspaceTrustStatusResponse,
            await client.request(
                "workspace/trust/decision",
                WorkspaceTrustDecisionParams(
                    session_id=self._state.session_id, cwd=cwd, decision=decision
                ),
            ),
        )


class LoopsResource:
    def __init__(
        self, connection: AppServerResourceConnection, state: ClientSessionState
    ) -> None:
        self._connection = connection
        self._state = state

    async def list(self) -> list[ScheduledLoop]:
        client = await self._connection.connect()
        response = validate_wire(
            LoopsListResponse,
            await client.request(
                "loops/list", LoopsListParams(session_id=self._state.session_id)
            ),
        )
        return list(response.loops)

    async def create(self, interval: str, prompt: str) -> ScheduledLoop:
        client = await self._connection.connect()
        response = validate_wire(
            LoopsCreateResponse,
            await client.request(
                "loops/create",
                LoopsCreateParams(
                    session_id=self._state.session_id, interval=interval, prompt=prompt
                ),
            ),
        )
        return response.loop

    async def delete(self, loop_id: str) -> ScheduledLoop:
        client = await self._connection.connect()
        response = validate_wire(
            LoopsDeleteResponse,
            await client.request(
                "loops/delete",
                LoopsDeleteParams(session_id=self._state.session_id, loop_id=loop_id),
            ),
        )
        return response.loop

    async def clear(self) -> int:
        client = await self._connection.connect()
        response = validate_wire(
            LoopsClearResponse,
            await client.request(
                "loops/clear", LoopsClearParams(session_id=self._state.session_id)
            ),
        )
        return response.count
