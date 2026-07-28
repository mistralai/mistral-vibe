from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from pydantic import JsonValue

from vibe.app_server._projection import project_history, project_session_log
from vibe.app_server._session_history import SessionHistory
from vibe.app_server._state import build_public_state
from vibe.app_server._utils import now_ms
from vibe.app_server.models import (
    PublicCallbackEntry,
    PublicCheckpointEntry,
    PublicEntryGenerationStatus,
    PublicHistoryEntry,
    PublicSessionState,
    PublicTurn,
    SessionLogSummary,
)
from vibe.core.agent_loop import AgentLoop


class SessionResources(Protocol):
    def restore_loops(self) -> None: ...

    def transfer_loops(self) -> None: ...


class ChildSessions(Protocol):
    def handoff_root(self, old_session_id: str, new_session_id: str) -> None: ...


class EventWatermark(Protocol):
    def __call__(self, session_id: str) -> int: ...


@dataclass(frozen=True, slots=True)
class SessionHandoff:
    old_session_id: str
    new_session_id: str
    state: PublicSessionState
    session_log: SessionLogSummary


class RootSessionCoordinator:
    def __init__(
        self,
        agent_loop: AgentLoop,
        resources: SessionResources,
        child_sessions: ChildSessions,
        event_watermark: EventWatermark,
        history: SessionHistory,
    ) -> None:
        self._agent_loop = agent_loop
        self._resources = resources
        self._child_sessions = child_sessions
        self._event_watermark = event_watermark
        self._attached_session_id: str | None = None
        self._history = history
        self._handoffs: dict[str, str] = {}

    @property
    def current_session_id(self) -> str:
        return self._agent_loop.session_id

    @property
    def attached_session_id(self) -> str | None:
        return self._attached_session_id

    def attach(self, session_id: str) -> None:
        self._attached_session_id = session_id

    def is_current(self, session_id: str) -> bool:
        return session_id == self.current_session_id

    def is_attached(self, session_id: str) -> bool:
        return self.is_current(session_id) and self._attached_session_id == session_id

    def routes_active_turn(self, session_id: str, turn: PublicTurn) -> bool:
        if self.is_attached(session_id):
            return True
        return (
            self._attached_session_id == self.current_session_id
            and self._resolve_handoff(session_id) == self.current_session_id
            and turn.session_id == self.current_session_id
        )

    def replace_from_core(self) -> None:
        self._history.replace(project_history(self._agent_loop))
        self._handoffs.clear()
        self._resources.restore_loops()

    def all_history(
        self, current_history: list[PublicHistoryEntry]
    ) -> list[PublicHistoryEntry]:
        return self._history.all(current_history)

    def public_state(
        self,
        *,
        current_history: list[PublicHistoryEntry],
        callbacks: list[PublicCallbackEntry],
        active_turn: PublicTurn | None,
        last_turn: PublicTurn | None,
        history_limit: int,
    ) -> PublicSessionState:
        state = build_public_state(
            self._agent_loop,
            history=self._history.base,
            current_history=current_history,
            callbacks=callbacks,
            active_turn=active_turn,
            last_turn=last_turn,
            history_limit=history_limit,
        )
        return state.model_copy(
            update={"event_id": self._event_watermark(state.session.id)}
        )

    def handoff_active_turn(
        self,
        old_session_id: str,
        *,
        current_history: list[PublicHistoryEntry],
        callbacks: list[PublicCallbackEntry],
        active_turn: PublicTurn,
        last_turn: PublicTurn | None = None,
        history_limit: int = 200,
    ) -> SessionHandoff:
        new_session_id = self._begin_handoff(old_session_id)
        self._history.replace(_rebind_history(self._history.base, new_session_id))
        state = self.public_state(
            current_history=current_history,
            callbacks=callbacks,
            active_turn=active_turn,
            last_turn=last_turn,
            history_limit=history_limit,
        )
        return SessionHandoff(
            old_session_id=old_session_id,
            new_session_id=new_session_id,
            state=state,
            session_log=project_session_log(self._agent_loop),
        )

    def replace_idle(
        self,
        old_session_id: str,
        *,
        current_history: list[PublicHistoryEntry],
        checkpoint_kind: str,
        checkpoint_message: str,
        checkpoint_details: JsonValue = None,
        history_limit: int = 200,
    ) -> SessionHandoff:
        new_session_id = self.current_session_id
        if old_session_id != new_session_id:
            new_session_id = self._begin_handoff(old_session_id)
        return self._checkpoint_idle(
            old_session_id,
            new_session_id,
            current_history=current_history,
            checkpoint_kind=checkpoint_kind,
            checkpoint_message=checkpoint_message,
            checkpoint_details=checkpoint_details,
            history_limit=history_limit,
        )

    def replace_idle_with_history(
        self,
        old_session_id: str,
        *,
        history: list[PublicHistoryEntry],
        checkpoint_kind: str,
        checkpoint_message: str,
        checkpoint_details: JsonValue = None,
        history_limit: int = 200,
    ) -> SessionHandoff:
        self._history.replace(history)
        return self.replace_idle(
            old_session_id,
            current_history=[],
            checkpoint_kind=checkpoint_kind,
            checkpoint_message=checkpoint_message,
            checkpoint_details=checkpoint_details,
            history_limit=history_limit,
        )

    def append_checkpoint(
        self,
        *,
        current_history: list[PublicHistoryEntry],
        kind: str,
        message: str,
        details: JsonValue = None,
        history_limit: int = 200,
    ) -> PublicSessionState:
        return self._checkpoint_state(
            self.current_session_id,
            current_history=current_history,
            kind=kind,
            message=message,
            details=details,
            history_limit=history_limit,
        )

    def _checkpoint_idle(
        self,
        old_session_id: str,
        new_session_id: str,
        *,
        current_history: list[PublicHistoryEntry],
        checkpoint_kind: str,
        checkpoint_message: str,
        checkpoint_details: JsonValue,
        history_limit: int,
    ) -> SessionHandoff:
        state = self._checkpoint_state(
            new_session_id,
            current_history=current_history,
            kind=checkpoint_kind,
            message=checkpoint_message,
            details=checkpoint_details,
            history_limit=history_limit,
        )
        return SessionHandoff(
            old_session_id=old_session_id,
            new_session_id=new_session_id,
            state=state,
            session_log=project_session_log(self._agent_loop),
        )

    def _checkpoint_state(
        self,
        session_id: str,
        *,
        current_history: list[PublicHistoryEntry],
        kind: str,
        message: str,
        details: JsonValue,
        history_limit: int,
    ) -> PublicSessionState:
        history = _rebind_history(self.all_history(current_history), session_id)
        timestamp = now_ms()
        history.append(
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
        self._history.replace(history)
        return self.public_state(
            current_history=[],
            callbacks=[],
            active_turn=None,
            last_turn=None,
            history_limit=history_limit,
        )

    def _begin_handoff(self, old_session_id: str) -> str:
        new_session_id = self.current_session_id
        if old_session_id == new_session_id:
            raise RuntimeError("Session handoff did not change the session ID")
        if self._attached_session_id == old_session_id:
            self._attached_session_id = new_session_id
        self._handoffs[old_session_id] = new_session_id
        self._child_sessions.handoff_root(old_session_id, new_session_id)
        self._resources.transfer_loops()
        return new_session_id

    def _resolve_handoff(self, session_id: str) -> str:
        seen: set[str] = set()
        while session_id in self._handoffs and session_id not in seen:
            seen.add(session_id)
            session_id = self._handoffs[session_id]
        return session_id


def rebind_history(
    history: list[PublicHistoryEntry], session_id: str
) -> list[PublicHistoryEntry]:
    return _rebind_history(history, session_id)


def _rebind_history(
    history: list[PublicHistoryEntry], session_id: str
) -> list[PublicHistoryEntry]:
    return [entry.model_copy(update={"session_id": session_id}) for entry in history]
