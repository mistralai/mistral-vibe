from __future__ import annotations

import asyncio
from typing import Any, Protocol

from vibe.core.config import VibeConfig
from vibe.core.nuage.remote_events_source import RemoteEventsSource
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestionArgs,
    Choice,
    Question,
)
from vibe.core.types import (
    AssistantEvent,
    BaseEvent,
    ReasoningEvent,
    ToolCallEvent,
    ToolStreamEvent,
    UserMessageEvent,
    WaitingForInputEvent,
)

_MIN_QUESTION_OPTIONS = 2
_MAX_QUESTION_OPTIONS = 4


class RemoteSessionUI(Protocol):
    async def on_remote_event(
        self, event: BaseEvent, loading_active: bool, loading_widget: Any
    ) -> None: ...
    async def on_remote_waiting_input(self, event: WaitingForInputEvent) -> None: ...
    async def on_remote_user_message_cleared_input(self) -> None: ...
    async def on_remote_stream_error(self, error: str) -> None: ...
    async def on_remote_stream_ended(self, msg_type: str, text: str) -> None: ...
    async def on_remote_finalize_streaming(self) -> None: ...
    async def remove_loading(self) -> None: ...
    async def ensure_loading(self, status: str = "Generating") -> None: ...
    @property
    def loading_widget(self) -> Any: ...


def is_progress_event(event: object) -> bool:
    return isinstance(
        event, (AssistantEvent, ReasoningEvent, ToolCallEvent, ToolStreamEvent)
    )


class RemoteSessionManager:
    def __init__(self) -> None:
        self._events_source: RemoteEventsSource | None = None
        self._stream_task: asyncio.Task | None = None
        self._pending_waiting_input: WaitingForInputEvent | None = None

    @property
    def is_active(self) -> bool:
        return self._events_source is not None

    @property
    def is_terminated(self) -> bool:
        if self._events_source is None:
            return False
        return self._events_source.is_terminated

    @property
    def is_waiting_for_input(self) -> bool:
        if self._events_source is None:
            return False
        return self._events_source.is_waiting_for_input

    @property
    def has_pending_input(self) -> bool:
        return self._pending_waiting_input is not None

    @property
    def session_id(self) -> str | None:
        if self._events_source is None:
            return None
        return self._events_source.session_id

    async def attach(self, session_id: str, config: VibeConfig) -> None:
        await self.detach()
        self._events_source = RemoteEventsSource(session_id=session_id, config=config)

    async def detach(self) -> None:
        await self._stop_stream()
        if self._events_source is not None:
            await self._events_source.close()
        self._events_source = None
        self._pending_waiting_input = None

    def validate_input(self) -> str | None:
        if self.is_terminated:
            return (
                "Remote session has ended. Use /clear to start a new session"
                " or /resume to attach to another."
            )
        if not self.is_waiting_for_input:
            return (
                "Remote session is not waiting for input. Please wait for the"
                " current task to complete."
            )
        return None

    async def send_prompt(self, message: str, *, require_source: bool = True) -> None:
        if self._events_source is None:
            if require_source:
                raise RuntimeError("No active remote session")
            return
        saved_pending = self._pending_waiting_input
        self._pending_waiting_input = None
        try:
            await self._events_source.send_prompt(message)
        except Exception:
            self._pending_waiting_input = saved_pending
            raise

    def cancel_pending_input(self) -> None:
        self._pending_waiting_input = None

    def build_question_args(
        self, event: WaitingForInputEvent
    ) -> AskUserQuestionArgs | None:
        if (
            not event.predefined_answers
            or len(event.predefined_answers) < _MIN_QUESTION_OPTIONS
        ):
            return None

        question = event.label or "Choose an answer"
        return AskUserQuestionArgs(
            questions=[
                Question(
                    question=question,
                    options=[
                        Choice(label=answer)
                        for answer in event.predefined_answers[:_MAX_QUESTION_OPTIONS]
                    ],
                )
            ]
        )

    def set_pending_input(self, event: WaitingForInputEvent) -> None:
        self._pending_waiting_input = event

    def start_stream(self, ui: RemoteSessionUI) -> None:
        if self._events_source is None:
            return
        if self._stream_task and not self._stream_task.done():
            return
        self._stream_task = asyncio.create_task(
            self._consume_stream(ui), name="remote-session-stream"
        )

    async def stop_stream(self) -> None:
        await self._stop_stream()

    def build_terminal_message(self) -> tuple[str, str]:
        if self._events_source is None:
            return ("info", "Remote session completed")
        if self._events_source.is_failed:
            return ("error", "Remote session failed")
        if self._events_source.is_canceled:
            return ("warning", "Remote session was canceled")
        return ("info", "Remote session completed")

    def cancel_stream_task(self) -> None:
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()

    async def _stop_stream(self) -> None:
        if self._stream_task is None or self._stream_task.done():
            self._stream_task = None
            return
        self._stream_task.cancel()
        try:
            await self._stream_task
        except asyncio.CancelledError:
            pass
        self._stream_task = None

    async def _consume_stream(self, ui: RemoteSessionUI) -> None:
        events_source = self._events_source
        if events_source is None:
            return
        await ui.ensure_loading("Generating")
        try:
            async for event in events_source.attach():
                if isinstance(event, WaitingForInputEvent):
                    await ui.remove_loading()
                    self._pending_waiting_input = event
                    await ui.on_remote_waiting_input(event)
                elif (
                    isinstance(event, UserMessageEvent)
                    and self._pending_waiting_input is not None
                ):
                    self._pending_waiting_input = None
                    await ui.on_remote_user_message_cleared_input()
                elif ui.loading_widget is None and is_progress_event(event):
                    await ui.ensure_loading()
                await ui.on_remote_event(
                    event,
                    loading_active=ui.loading_widget is not None,
                    loading_widget=ui.loading_widget,
                )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            await ui.on_remote_stream_error(f"Remote stream stopped: {e}")
        finally:
            await ui.on_remote_finalize_streaming()
            await ui.remove_loading()
            self._stream_task = None
            self._pending_waiting_input = None
            if events_source.is_terminated:
                msg_type, text = self.build_terminal_message()
                await ui.on_remote_stream_ended(msg_type, text)
