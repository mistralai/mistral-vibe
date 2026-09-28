from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
import time
from typing import Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.worker import WorkerCancelled

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
    wait_until,
)
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend, FakeInterruptedStreamingBackend
from vibe.app_server.events import CallbackRequested, ChildSessionUpdated
from vibe.app_server.models import (
    ApprovalCallbackDetail,
    ApprovalCallbackOutput,
    ApprovalDecision,
    ApprovalDecisionType,
    EffectCallDisplay,
    GenericEffectDetail,
    IdleSessionStatus,
    OpenCallbackState,
    PublicCallbackEntry,
    PublicChildSession,
    PublicEntryGenerationStatus,
    PublicError,
    PublicHistoryEntry,
    PublicMessageEntry,
    PublicMessageSource,
    QuestionChoice,
    RunningSessionStatus,
    TextContentBlock,
    TokenUsage,
    TurnErrorCode,
    UserAnswer,
    UserInputCallbackDetail,
    UserInputCallbackOutput,
    UserQuestion,
    UserQuestionRequest,
    UserQuestionResult,
    WorkspaceTrustDetails,
)
from vibe.app_server.protocol import (
    ConfigWriteOpWire,
    SessionHistoryListResponse,
    WorkspaceTrustStatusResponse,
)
from vibe.app_server.session import AppServerSession, AppServerTurnError
from vibe.cli.textual_ui import startup
from vibe.cli.textual_ui.app import DOUBLE_ESC_DELAY, VibeApp
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.cli.textual_ui.widgets.chat_input.body import ChatInputBody
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.chat_input.subagent_list import SubagentList
from vibe.cli.textual_ui.widgets.chat_input.text_area import ChatTextArea
from vibe.cli.textual_ui.widgets.context_progress import ContextProgress, TokenState
from vibe.cli.textual_ui.widgets.loading import DEFAULT_LOADING_STATUS, LoadingWidget
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    ErrorMessage,
    InterruptMessage,
    ReasoningMessage,
    SlashCommandMessage,
    UserMessage,
    UserMessageSeverity,
)
from vibe.cli.textual_ui.widgets.question_app import QuestionApp
from vibe.cli.textual_ui.widgets.subagent_transcripts import SubagentTranscripts
from vibe.core.config import SessionLoggingConfig
from vibe.core.types import Role, ScheduledLoop, UserMessageEvent
from vibe.setup.trusted_folders.trust_folder_dialog import TrustFolderApp
from vibe.utils import VIBE_WARNING_TAG
from vibe.utils.retry_prompt import build_retry_prompt


def _callback(detail: ApprovalCallbackDetail | UserInputCallbackDetail):
    return PublicCallbackEntry(
        id="callback:callback-1",
        session_id="session-1",
        turn_id="turn-1",
        created_at=1,
        updated_at=1,
        generation_status=PublicEntryGenerationStatus.IN_PROGRESS,
        callback_id="callback-1",
        title="Input required",
        detail=detail,
        state=OpenCallbackState(),
    )


def _turn_error(code: TurnErrorCode) -> AppServerTurnError:
    return AppServerTurnError(PublicError(message="Network error", code=code))


async def _wait_for_retry_error(app: VibeApp, pilot) -> None:
    assert await wait_until(
        pilot,
        lambda: any("/retry" in str(error._error) for error in app.query(ErrorMessage)),
    )


@pytest.mark.asyncio
async def test_turn_finalization_keeps_ready_subagents_visible() -> None:
    agent_loop = build_test_agent_loop()
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        assert await wait_until(pilot, lambda: app._app_server is not None)
        child = PublicChildSession(
            id="child-1",
            name="test-audit",
            agent_type="explore",
            status=RunningSessionStatus(active_turn_id="turn-child"),
            created_at=1,
            updated_at=1,
        )
        app.app_server.state.child_sessions = [child]
        app._refresh_subagent_list()
        assert app.query_one(SubagentList).display

        app.app_server.state.child_sessions = [
            child.model_copy(update={"status": IdleSessionStatus(), "updated_at": 2})
        ]

        await app._finalize_turn_ui(notify_complete=False)
        await pilot.pause()

        widget = app.query_one(SubagentList)
        assert widget.display
        assert widget.option_count == 2
        assert "[ready]" in str(widget.get_option_at_index(1).prompt)


@pytest.mark.asyncio
async def test_ready_subagent_transcript_explains_next_actions_once() -> None:
    app = build_test_vibe_app(agent_loop=build_test_agent_loop())

    async with app.run_test():
        running = PublicChildSession(
            id="child-1",
            name="test-audit",
            agent_type="explore",
            status=RunningSessionStatus(active_turn_id="turn-child"),
            created_at=1,
            updated_at=1,
        )
        ready = running.model_copy(
            update={"status": IdleSessionStatus(), "updated_at": 2}
        )
        app.app_server.state.child_sessions = [ready]

        await app._handle_turn_event(ChildSessionUpdated(ready))
        await app._handle_turn_event(ChildSessionUpdated(ready))

        container = app.query_one(SubagentTranscripts).transcript_container(ready.id)
        assert container is None

        await app._handle_turn_event(ChildSessionUpdated(running))
        await app._handle_turn_event(ChildSessionUpdated(ready))
        await app._handle_turn_event(ChildSessionUpdated(ready))

        container = app.query_one(SubagentTranscripts).transcript_container(ready.id)
        assert container is not None
        ready_messages = [
            message
            for message in container.query(UserMessage)
            if message.get_content().startswith("This subagent is ready")
        ]
        assert len(ready_messages) == 1
        assert ready_messages[0].severity is UserMessageSeverity.INFO
        assert (
            ready_messages[0]
            .get_content()
            .endswith("ask the main agent to give it a new goal or stop it.")
        )

        await app._handle_turn_event(ChildSessionUpdated(running))
        await app._handle_turn_event(ChildSessionUpdated(ready))

        ready_messages = [
            message
            for message in container.query(UserMessage)
            if message.get_content().startswith("This subagent is ready")
        ]
        assert len(ready_messages) == 2


@pytest.mark.asyncio
async def test_all_ready_subagents_do_not_add_an_info_message_to_main() -> None:
    app = build_test_vibe_app(agent_loop=build_test_agent_loop())

    async with app.run_test():
        first_running = PublicChildSession(
            id="child-1",
            name="test-audit",
            agent_type="explore",
            status=RunningSessionStatus(active_turn_id="turn-child-1"),
            created_at=1,
            updated_at=1,
        )
        second_running = PublicChildSession(
            id="child-2",
            name="test-review",
            agent_type="explore",
            status=RunningSessionStatus(active_turn_id="turn-child-2"),
            created_at=1,
            updated_at=1,
        )
        first_ready = first_running.model_copy(
            update={"status": IdleSessionStatus(), "updated_at": 2}
        )
        second_ready = second_running.model_copy(
            update={"status": IdleSessionStatus(), "updated_at": 2}
        )

        app._pending_turn = True
        app.app_server.state.child_sessions = [first_running, second_running]
        await app._handle_turn_event(ChildSessionUpdated(first_running))
        await app._handle_turn_event(ChildSessionUpdated(second_running))

        app.app_server.state.child_sessions = [first_ready, second_ready]
        await app._handle_turn_event(ChildSessionUpdated(first_ready))
        await app._handle_turn_event(ChildSessionUpdated(second_ready))
        await app._finalize_turn_ui(notify_complete=False)

        main_messages = [
            message
            for message in app._messages_area.query(UserMessage)
            if message.severity is UserMessageSeverity.INFO
        ]
        assert main_messages == []


@pytest.mark.asyncio
async def test_subagent_transcript_switch_reuses_the_mounted_view(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = build_test_vibe_app(
        agent_loop=build_test_agent_loop(), history_file=tmp_path / "history"
    )
    child = PublicChildSession(
        id="child-1",
        name="test-audit",
        agent_type="explore",
        status=RunningSessionStatus(active_turn_id="turn-child"),
        created_at=1,
        updated_at=1,
    )
    history = [
        PublicMessageEntry(
            id="message-1",
            session_id=child.id,
            turn_id="turn-1",
            created_at=1,
            updated_at=1,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            role="user",
            content=[TextContentBlock(text="Inspect the code")],
        )
    ]

    async with app.run_test() as pilot:
        app.app_server.state.child_sessions = [child]
        read_history = AsyncMock(return_value=history)
        monkeypatch.setattr(
            app.app_server.resources.sessions, "get_session_history", read_history
        )

        app._refresh_subagent_list()
        input_widget = app.query_one(ChatTextArea)
        input_body = app.query_one(ChatInputBody)
        subagent_list = app.query_one(SubagentList)
        assert input_body.history is not None
        input_body.history.add("Previous prompt")
        input_widget.focus()

        await pilot.press("up")
        assert input_widget.text == "Previous prompt"
        await pilot.press("down")
        assert input_widget.text == ""
        assert app.focused is input_widget

        input_widget.insert("Draft prompt")
        await pilot.press("down")
        assert app.focused is subagent_list
        assert subagent_list.highlighted == 0
        assert input_widget.text == "Draft prompt"
        await pilot.press("up")
        assert app.focused is input_widget
        assert input_widget.text == "Draft prompt"
        input_widget.clear()

        await pilot.press("down")
        assert app.focused is subagent_list
        assert subagent_list.highlighted == 0
        await pilot.press("up")
        assert app.focused is input_widget

        await pilot.press("down", "down")
        assert app._viewed_subagent_id is None
        assert app.focused is subagent_list
        assert subagent_list.highlighted == 1

        await pilot.press("enter")
        assert await wait_until(pilot, lambda: app._viewed_subagent_id == child.id)
        assert await wait_until(pilot, lambda: bool(app.query(UserMessage)))
        transcripts = app.query_one(SubagentTranscripts)
        child_container = transcripts.transcript_container(child.id)
        assert child_container is not None
        root_messages = app.query_one("#messages")
        input_box = app.query_one("#input-box")
        assert app.focused is subagent_list
        assert subagent_list.highlighted == 1
        assert not root_messages.display
        assert not input_box.display

        updated_history = [
            history[0].model_copy(
                update={"content": [TextContentBlock(text="Inspect the updated code")]}
            )
        ]
        read_history.return_value = updated_history
        await app._handle_turn_event(
            ChildSessionUpdated(child.model_copy(update={"updated_at": 2}))
        )
        assert await wait_until(pilot, lambda: read_history.await_count == 2)
        assert await wait_until(
            pilot,
            lambda: any(
                "Inspect the updated code" in message.get_content()
                for message in app.query(UserMessage)
            ),
        )

        await pilot.press("up")
        assert app._viewed_subagent_id == child.id
        assert app.focused is subagent_list
        assert subagent_list.highlighted == 0

        await pilot.press("enter")
        assert await wait_until(pilot, lambda: app._viewed_subagent_id is None)
        assert app.focused is subagent_list
        assert subagent_list.highlighted == 0
        assert input_box.display
        assert input_widget.text == ""

        await pilot.press("up")
        assert app.focused is input_widget
        assert input_widget.text == ""

        await pilot.press("down", "down")
        assert app._viewed_subagent_id is None
        await pilot.press("enter")
        assert await wait_until(pilot, lambda: app._viewed_subagent_id == child.id)
        assert await wait_until(pilot, lambda: read_history.await_count >= 3)
        history_reads_before_shortcuts = read_history.await_count
        force_quit = MagicMock()
        monkeypatch.setattr(app, "_force_quit", force_quit)

        await pilot.press("ctrl+c")

        assert app._viewed_subagent_id == child.id
        assert await wait_until(
            pilot,
            lambda: (
                len([
                    message
                    for message in child_container.query(UserMessage)
                    if message.get_content().startswith(
                        "You can't interact with a subagent directly."
                    )
                ])
                == 1
            ),
        )
        local_messages = [
            message
            for message in child_container.query(UserMessage)
            if message.get_content().startswith(
                "You can't interact with a subagent directly."
            )
        ]
        assert all(
            message.get_content()
            == "You can't interact with a subagent directly. Return to Main "
            "conversation and ask the main agent to stop it."
            for message in local_messages
        )
        assert all(
            message.severity is UserMessageSeverity.ERROR for message in local_messages
        )
        assert read_history.await_count == history_reads_before_shortcuts
        assert not root_messages.display
        assert not input_box.display

        await pilot.press("ctrl+c")
        force_quit.assert_called_once_with()
        assert (
            len([
                message
                for message in child_container.query(UserMessage)
                if message.get_content().startswith(
                    "You can't interact with a subagent directly."
                )
            ])
            == 1
        )

        subagent_list.post_message(SubagentList.Selected(None))
        assert await wait_until(pilot, lambda: app._viewed_subagent_id is None)
        assert root_messages.display
        assert input_box.display

        subagent_list.post_message(SubagentList.Selected(child.id))
        assert await wait_until(pilot, lambda: app._viewed_subagent_id == child.id)

        assert transcripts.transcript_container(child.id) is child_container
        assert app.focused is subagent_list
        assert subagent_list.highlighted == 1
        assert root_messages.display is False
        assert input_box.display is False


@pytest.mark.asyncio
async def test_escape_returns_from_subagent_view_without_a_local_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(agent_loop=build_test_agent_loop())
    child = PublicChildSession(
        id="child-1",
        name="test-audit",
        agent_type="explore",
        status=RunningSessionStatus(active_turn_id="turn-child"),
        created_at=1,
        updated_at=1,
    )

    async with app.run_test() as pilot:
        app.app_server.state.child_sessions = [child]
        monkeypatch.setattr(
            app.app_server.resources.sessions,
            "get_session_history",
            AsyncMock(return_value=[]),
        )
        app._refresh_subagent_list()
        await app._show_subagent_chat(child.id)

        await pilot.press("escape")

        assert await wait_until(pilot, lambda: app._viewed_subagent_id is None)
        assert not list(app.query_one(SubagentTranscripts).query(".user-message-error"))


@pytest.mark.asyncio
async def test_escape_from_subagent_view_cancels_pending_quit_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(agent_loop=build_test_agent_loop())
    child = PublicChildSession(
        id="child-1",
        name="test-audit",
        agent_type="explore",
        status=RunningSessionStatus(active_turn_id="turn-child"),
        created_at=1,
        updated_at=1,
    )

    async with app.run_test() as pilot:
        app.app_server.state.child_sessions = [child]
        monkeypatch.setattr(
            app.app_server.resources.sessions,
            "get_session_history",
            AsyncMock(return_value=[]),
        )
        force_quit = MagicMock()
        monkeypatch.setattr(app, "_force_quit", force_quit)
        app._refresh_subagent_list()
        await app._show_subagent_chat(child.id)

        await pilot.press("ctrl+c", "escape", "ctrl+c")

        assert await wait_until(pilot, lambda: app._viewed_subagent_id is None)
        force_quit.assert_not_called()
        assert app._quit_manager.is_confirmed("Ctrl+C")

        await pilot.press("ctrl+c")

        force_quit.assert_called_once_with()


@pytest.mark.asyncio
async def test_reset_subagent_views_waits_for_refresh_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(agent_loop=build_test_agent_loop())
    worker_finished = asyncio.Event()
    worker = MagicMock()

    async def wait_for_worker() -> None:
        await worker_finished.wait()
        raise WorkerCancelled("Refresh cancelled")

    worker.wait = AsyncMock(side_effect=wait_for_worker)

    async with app.run_test() as pilot:
        clear = AsyncMock()
        monkeypatch.setattr(app._subagent_transcripts, "clear", clear)
        app._subagent_refresh_worker = worker

        reset = asyncio.create_task(app._reset_subagent_views())

        assert await wait_until(pilot, lambda: worker.cancel.called)
        clear.assert_not_awaited()

        worker_finished.set()
        await reset

        worker.wait.assert_awaited_once()
        clear.assert_awaited_once()


@pytest.mark.asyncio
async def test_subagent_status_list_can_be_disabled_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(agent_loop=build_test_agent_loop())
    child = PublicChildSession(
        id="child-1",
        name="test-audit",
        agent_type="explore",
        status=RunningSessionStatus(active_turn_id="turn-child"),
        created_at=1,
        updated_at=1,
    )

    async with app.run_test() as pilot:
        app.app_server.state.child_sessions = [child]
        monkeypatch.setattr(
            app.app_server.resources.sessions,
            "get_session_history",
            AsyncMock(return_value=[]),
        )
        app._refresh_subagent_list()
        assert app.query_one(SubagentList).display
        await app._show_subagent_chat(child.id)
        assert app._viewed_subagent_id == child.id

        await app._run_config_patch(
            [
                ConfigWriteOpWire(
                    op="set", path="/show_subagent_status_list", value=False
                )
            ],
            "hide subagent status list",
        )

        assert await wait_until(pilot, lambda: not app.query_one(SubagentList).display)
        assert app._viewed_subagent_id is None


@pytest.mark.asyncio
async def test_subagent_view_uses_loading_widget_and_child_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(agent_loop=build_test_agent_loop())
    child = PublicChildSession(
        id="child-1",
        name="test-audit",
        agent_type="explore",
        status=RunningSessionStatus(active_turn_id="turn-child"),
        context_usage=TokenUsage(input_tokens=12_500, total_tokens=12_500),
        created_at=1,
        updated_at=1,
    )

    async with app.run_test():
        monkeypatch.setattr(
            app.app_server.resources.sessions,
            "get_session_history",
            AsyncMock(return_value=[]),
        )
        main_tokens = TokenState(
            max_tokens=app.app_server.resources.runtime.context_window,
            current_tokens=app.app_server.resources.runtime.stats.context_tokens,
        )
        app.app_server.state.child_sessions = [child]
        await app._ensure_loading_widget()
        await app._handle_turn_event(ChildSessionUpdated(child))
        await app._show_subagent_chat(child.id)

        child_loading = app._subagent_loading_widget
        context = app.query_one(ContextProgress)
        assert child_loading is not None
        assert child_loading.display
        assert child_loading.base_status == "Running"
        assert context.tokens == TokenState(
            max_tokens=main_tokens.max_tokens, current_tokens=12_500
        )
        assert app._loading_widget is not None
        assert not app._loading_widget.display

        stats_updated = MagicMock()
        stats_updated.params.context_window = 99_999
        stats_updated.params.stats.context_tokens = 88_888
        app._update_context_progress(stats_updated)
        assert context.tokens.current_tokens == 12_500

        ready = child.model_copy(
            update={
                "status": IdleSessionStatus(),
                "context_usage": TokenUsage(input_tokens=18_000, total_tokens=18_000),
                "updated_at": 2,
            }
        )
        app.app_server.state.child_sessions = [ready]
        await app._handle_turn_event(ChildSessionUpdated(ready))

        assert not child_loading.display
        assert context.tokens.current_tokens == 18_000

        app._show_main_chat()

        assert not child_loading.display
        assert app._loading_widget.display
        assert context.tokens == main_tokens


@pytest.mark.asyncio
async def test_subagent_transcript_loads_complete_history_before_the_latest_tail(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    app = build_test_vibe_app(
        agent_loop=build_test_agent_loop(), history_file=tmp_path / "history"
    )
    child = PublicChildSession(
        id="child-1",
        name="test-audit",
        agent_type="explore",
        status=RunningSessionStatus(active_turn_id="turn-child"),
        created_at=1,
        updated_at=1,
    )

    def message(
        index: int,
        role: Literal["assistant", "user"],
        source: PublicMessageSource | None = None,
    ) -> PublicMessageEntry:
        return PublicMessageEntry(
            id=f"message-{index}",
            session_id=child.id,
            turn_id="turn-child",
            created_at=index,
            updated_at=index,
            generation_status=PublicEntryGenerationStatus.COMPLETED,
            role=role,
            source=source,
            content=[TextContentBlock(text=f"message {index}")],
        )

    context: list[PublicHistoryEntry] = [
        message(0, "user", "turn_start"),
        message(1, "assistant"),
        message(2, "user", "turn_steer"),
    ]
    tail = [message(index, "assistant") for index in range(3, 23)]

    async with app.run_test() as pilot:
        app.app_server.state.child_sessions = [child]
        read_history = AsyncMock(return_value=tail)
        list_history = AsyncMock(return_value=SessionHistoryListResponse(items=context))
        monkeypatch.setattr(
            app.app_server.resources.sessions, "get_session_history", read_history
        )
        monkeypatch.setattr(
            app.app_server.resources.sessions, "list_history", list_history
        )

        app._refresh_subagent_list()
        await app._show_subagent_chat(child.id)

        assert await wait_until(pilot, lambda: list_history.await_count == 1)
        assert await wait_until(
            pilot,
            lambda: (
                [message.get_content() for message in app.query(UserMessage)]
                == ["message 0", "message 2"]
            ),
        )
        container = app.query_one(SubagentTranscripts).transcript_container(child.id)
        assert container is not None
        assert len(container.children) == len(context) + len(tail)
        list_history.assert_awaited_once_with(
            session_id=child.id, before=tail[0].id, limit=500
        )


@pytest.mark.asyncio
async def test_approval_callback_opens_from_public_protocol() -> None:
    app = MagicMock()
    app._active_callback = None
    app._pending_local_question = None
    app._pending_callbacks = deque()
    app._wait_for_typing_pause = AsyncMock()
    app._switch_to_approval_app = AsyncMock()
    effect = GenericEffectDetail(
        tool_name="example",
        input={"value": "ok"},
        display=EffectCallDisplay(summary="example", status_text="Running"),
    )
    callback = _callback(ApprovalCallbackDetail(effect=effect))

    await VibeApp._show_callback(app, callback)

    assert app._active_callback is callback
    app._switch_to_approval_app.assert_awaited_once_with(effect, [], [], None)


@pytest.mark.asyncio
async def test_overlapping_callbacks_are_queued_until_the_active_one_resolves() -> None:
    app = MagicMock()
    first = _callback(
        UserInputCallbackDetail(
            request=UserQuestionRequest(
                questions=[
                    UserQuestion(
                        question="First?",
                        options=[
                            QuestionChoice(label="Yes"),
                            QuestionChoice(label="No"),
                        ],
                    )
                ]
            )
        )
    )
    second = first.model_copy(
        update={"id": "callback:callback-2", "callback_id": "callback-2"}
    )
    app._active_callback = first
    app._pending_callbacks = deque()

    await VibeApp._show_callback(app, second)

    assert list(app._pending_callbacks) == [second]


@pytest.mark.asyncio
async def test_callback_claim_is_atomic_during_typing_debounce() -> None:
    app = MagicMock()
    first = _callback(
        UserInputCallbackDetail(
            request=UserQuestionRequest(
                questions=[
                    UserQuestion(
                        question="First?",
                        options=[
                            QuestionChoice(label="Yes"),
                            QuestionChoice(label="No"),
                        ],
                    )
                ]
            )
        )
    )
    second = first.model_copy(
        update={"id": "callback:callback-2", "callback_id": "callback-2"}
    )
    release = asyncio.Event()
    app._active_callback = None
    app._pending_local_question = None
    app._pending_callbacks = deque()
    app._wait_for_typing_pause = AsyncMock(side_effect=release.wait)
    app._switch_to_question_app = AsyncMock()

    first_task = asyncio.create_task(VibeApp._show_callback(app, first))
    await asyncio.sleep(0)
    await VibeApp._show_callback(app, second)

    assert app._active_callback is first
    assert list(app._pending_callbacks) == [second]
    release.set()
    await first_task


@pytest.mark.asyncio
async def test_callback_waits_behind_local_question() -> None:
    app = MagicMock()
    callback = _callback(
        UserInputCallbackDetail(
            request=UserQuestionRequest(
                questions=[
                    UserQuestion(
                        question="Server question?",
                        options=[
                            QuestionChoice(label="Yes"),
                            QuestionChoice(label="No"),
                        ],
                    )
                ]
            )
        )
    )
    app._active_callback = None
    app._pending_local_question = asyncio.get_running_loop().create_future()
    app._pending_callbacks = deque()

    await VibeApp._show_callback(app, callback)

    assert app._active_callback is None
    assert list(app._pending_callbacks) == [callback]


@pytest.mark.asyncio
async def test_answering_active_callback_opens_the_next_queued_callback() -> None:
    app = MagicMock()
    first = _callback(
        UserInputCallbackDetail(
            request=UserQuestionRequest(
                questions=[
                    UserQuestion(
                        question="First?",
                        options=[
                            QuestionChoice(label="Yes"),
                            QuestionChoice(label="No"),
                        ],
                    )
                ]
            )
        )
    )
    second = first.model_copy(
        update={"id": "callback:callback-2", "callback_id": "callback-2"}
    )
    app._active_callback = first
    app._pending_callbacks = deque([second])
    app.app_server.respond_to_callback = AsyncMock()
    app._show_callback = AsyncMock()

    output = UserInputCallbackOutput(
        result=UserQuestionResult(answers=[], cancelled=True)
    )
    await VibeApp._respond_to_active_callback(app, output)

    app.app_server.respond_to_callback.assert_awaited_once_with(
        first.callback_id, output
    )
    app._show_callback.assert_awaited_once_with(second)


@pytest.mark.asyncio
async def test_question_answer_responds_to_public_callback() -> None:
    app = MagicMock()
    app._active_callback = _callback(
        UserInputCallbackDetail(
            request=UserQuestionRequest(
                questions=[
                    UserQuestion(
                        question="Ship it?",
                        options=[
                            QuestionChoice(label="Yes"),
                            QuestionChoice(label="No"),
                        ],
                    )
                ]
            )
        )
    )
    app._respond_to_active_callback = AsyncMock()
    answer = UserAnswer(question="Ship it?", answer="Yes")

    await VibeApp.on_question_app_answered(app, QuestionApp.Answered([answer]))

    app._respond_to_active_callback.assert_awaited_once_with(
        UserInputCallbackOutput(
            result=UserQuestionResult(answers=[answer], cancelled=False)
        )
    )


def _approval_callback(callback_id: str) -> PublicCallbackEntry:
    effect = GenericEffectDetail(
        tool_name="example",
        input={"value": "ok"},
        display=EffectCallDisplay(
            summary="example", status_text="Waiting for approval to run example"
        ),
    )
    callback = _callback(ApprovalCallbackDetail(effect=effect))
    return callback.model_copy(
        update={"id": f"callback:{callback_id}", "callback_id": callback_id}
    )


@pytest.mark.asyncio
async def test_approval_callback_replaces_loading_status_before_typing_pause() -> None:
    app = MagicMock()
    callback = _approval_callback("callback-1")
    loading = LoadingWidget(status=DEFAULT_LOADING_STATUS)
    release_typing_pause = asyncio.Event()
    app._active_callback = None
    app._pending_local_question = None
    app._pending_callbacks = deque()
    app._loading_widget = loading
    app._ensure_loading_widget = AsyncMock()
    app._wait_for_typing_pause = AsyncMock(side_effect=release_typing_pause.wait)
    app._switch_to_approval_app = AsyncMock()

    show = asyncio.create_task(VibeApp._show_callback(app, callback))
    await asyncio.sleep(0)

    assert loading.base_status == "Waiting for approval to run example"
    assert loading._pause_start is not None
    app._switch_to_approval_app.assert_not_awaited()

    release_typing_pause.set()
    await show


@pytest.mark.asyncio
async def test_answering_final_callback_restores_loading_progress() -> None:
    app = MagicMock()
    callback = _approval_callback("callback-1")
    loading = LoadingWidget(status="Running command")
    loading.begin_action_required("Waiting for approval to run example")
    app._active_callback = callback
    app._pending_callbacks = deque()
    app._loading_widget = loading
    app.app_server.respond_to_callback = AsyncMock()
    app._switch_to_input_app = AsyncMock()
    output = ApprovalCallbackOutput(
        decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
    )

    await VibeApp._respond_to_active_callback(app, output)

    assert loading.base_status == "Running command"
    assert loading._pause_start is None
    app._switch_to_input_app.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_resolving_a_callback_swaps_to_the_next_without_duplicate_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    agent_loop = build_test_agent_loop(config=config)
    app = build_test_vibe_app(agent_loop=agent_loop)
    first = _approval_callback("callback-1")
    second = _approval_callback("callback-2")

    async with app.run_test() as pilot:
        assert await wait_until(pilot, lambda: app._app_server is not None)
        monkeypatch.setattr(app.app_server, "respond_to_callback", AsyncMock())

        await app._handle_turn_event(CallbackRequested(first))
        assert await wait_until(pilot, lambda: app._active_callback is first)
        assert len(app.query(ApprovalApp)) == 1
        assert app._loading_widget is not None
        assert app._loading_widget.base_status == "Waiting for approval to run example"
        paused_at = app._loading_widget._pause_start
        assert paused_at is not None

        await app._handle_turn_event(CallbackRequested(second))
        await pilot.pause()
        assert list(app._pending_callbacks) == [second]
        assert len(app.query(ApprovalApp)) == 1

        await app._respond_to_active_callback(
            ApprovalCallbackOutput(
                decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
            )
        )
        assert await wait_until(pilot, lambda: app._active_callback is second)
        assert len(app.query(ApprovalApp)) == 1
        assert app._loading_widget is not None
        assert app._loading_widget._pause_start == paused_at


@pytest.mark.asyncio
async def test_workspace_trust_round_trips_through_app_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = MagicMock()
    host.cwd = "/workspace"
    host.trust_status = AsyncMock(
        return_value=WorkspaceTrustStatusResponse(
            status="untrusted",
            details=WorkspaceTrustDetails(
                cwd="/workspace",
                detected_files=["AGENTS.md"],
                settings_path="/home/user/.vibe/trusted_folders.toml",
                available_decisions=["trust_cwd", "decline"],
            ),
        )
    )
    host.decide_trust = AsyncMock(
        return_value=WorkspaceTrustStatusResponse(status="trusted")
    )
    monkeypatch.setattr(
        TrustFolderApp, "run_trust_dialog_async", AsyncMock(return_value="trust_cwd")
    )
    assert await startup._resolve_workspace_trust(host) == (True, True)

    host.trust_status.assert_awaited_once_with("/workspace")
    host.decide_trust.assert_awaited_once_with("trust_cwd", cwd="/workspace")


@pytest.mark.asyncio
async def test_escape_interrupts_unsolicited_server_turn(tmp_path: Path) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    agent_loop = build_test_agent_loop(config=config)
    started = asyncio.Event()
    interrupted = asyncio.Event()

    async def blocking_act(msg: str, **_kwargs):
        yield UserMessageEvent(content=msg, message_id="scheduled-user")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            interrupted.set()

    agent_loop.act = blocking_act
    metadata = agent_loop.session_logger.session_metadata
    assert metadata is not None
    now = time.time()
    metadata.loops = [
        ScheduledLoop(
            id="scheduled-1",
            interval_seconds=30,
            prompt="scheduled prompt",
            next_fire_at=now - 1,
            created_at=now - 31,
        )
    ]
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert await wait_until(pilot, lambda: app.app_server.turn_active)
        assert app._agent_task is None

        await pilot.press("escape")

        await asyncio.wait_for(interrupted.wait(), timeout=2)
        assert await wait_until(pilot, lambda: not app.app_server.turn_active)


@pytest.mark.asyncio
async def test_slow_interrupt_warns_without_abandoning_the_turn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=SessionLoggingConfig(enabled=True, save_dir=str(tmp_path))
    )
    agent_loop = build_test_agent_loop(config=config)
    started = asyncio.Event()

    async def blocking_act(msg: str, **_kwargs):
        yield UserMessageEvent(content=msg, message_id="scheduled-user")
        started.set()
        await asyncio.Event().wait()

    agent_loop.act = blocking_act
    # A scheduled loop makes the server promote the turn, so it is driven server-side
    # (`turn_active`, no `_agent_task`) -- the branch that calls interrupt() directly.
    metadata = agent_loop.session_logger.session_metadata
    assert metadata is not None
    now = time.time()
    metadata.loops = [
        ScheduledLoop(
            id="scheduled-1",
            interval_seconds=30,
            prompt="scheduled prompt",
            next_fire_at=now - 1,
            created_at=now - 31,
        )
    ]
    monkeypatch.setattr(
        "vibe.cli.textual_ui.app.SLOW_INTERRUPT_HINT_DELAY", DOUBLE_ESC_DELAY
    )
    release = asyncio.Event()

    async def slow_interrupt(self: AppServerSession) -> None:
        await release.wait()

    monkeypatch.setattr(AppServerSession, "interrupt", slow_interrupt)
    app = build_test_vibe_app(agent_loop=agent_loop)
    warnings: list[str] = []
    monkeypatch.setattr(
        type(app),
        "notify",
        lambda self, message, **kwargs: warnings.append(str(message)),
    )

    async with app.run_test() as pilot:
        await asyncio.wait_for(started.wait(), timeout=2)
        assert await wait_until(pilot, lambda: app.app_server.turn_active)

        await pilot.press("escape")
        assert await wait_until(
            pilot, lambda: any("force quit" in text for text in warnings)
        )

        # Warned, but the request is still in flight -- nothing is torn down and no
        # InterruptMessage claims a turn ended that is still running.
        assert app._interrupt_requested is True
        assert app._loading_widget is not None
        assert not app.query(InterruptMessage)

        release.set()
        assert await wait_until(pilot, lambda: bool(app.query(InterruptMessage)))
        assert await wait_until(pilot, lambda: app._interrupt_requested is False)


def test_backend_error_message_hints_at_retry() -> None:
    app = MagicMock()
    app._retry_hint = VibeApp._retry_hint

    message = VibeApp._resolve_turn_error_message(
        app, _turn_error(TurnErrorCode.BACKEND_ERROR)
    )

    assert "/retry [additional instructions]" in message
    assert "Network error" in message


def test_internal_error_message_does_not_hint_at_retry() -> None:
    app = MagicMock()

    message = VibeApp._resolve_turn_error_message(
        app, _turn_error(TurnErrorCode.INTERNAL_ERROR)
    )

    assert "/retry" not in message


@pytest.mark.asyncio
async def test_incomplete_stream_retries_and_reuses_assistant_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = MagicMock()
    monkeypatch.setattr("vibe.cli.textual_ui.app.capture_sentry_exception", capture)
    backend = FakeBackend([
        [mock_llm_chunk(content="Ran three dummy read-only", stop_reason=None)],
        [mock_llm_chunk(content=" tool calls successfully.")],
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("hi"))
        assert await wait_until(pilot, lambda: len(backend.requests_messages) == 2)
        assert await wait_until(pilot, lambda: not app._agent_job_active())

        assert len(app.query(AssistantMessage)) == 1
        assert (
            app.query_one(AssistantMessage).get_content()
            == "Ran three dummy read-only tool calls successfully."
        )
        assert len(app.query(ErrorMessage)) == 0
        assert len(app.query(SlashCommandMessage)) == 0

    # A recovered retry is a transient blip; it must not reach Sentry.
    assert capture.call_count == 0

    retry_message = backend.requests_messages[-1][-1]
    assert retry_message.injected is True
    assert retry_message.content == build_retry_prompt("")


@pytest.mark.asyncio
async def test_incomplete_stream_hides_error_while_retrying() -> None:
    gate = asyncio.Event()

    class GatedRecoveryBackend(FakeBackend):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.calls = 0
            self.second_started = asyncio.Event()

        async def complete_streaming(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                self.second_started.set()
                await gate.wait()
            async for chunk in super().complete_streaming(**kwargs):
                yield chunk

    backend = GatedRecoveryBackend([
        [mock_llm_chunk(content="partial", stop_reason=None)],
        [mock_llm_chunk(content=" recovered.")],
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("hi"))

        # Wait until the automatic retry is in flight, then confirm no error is
        # surfaced while we are still retrying and that the loader says so.
        assert await wait_until(pilot, backend.second_started.is_set)
        assert len(app.query(ErrorMessage)) == 0
        assert app._loading_widget is not None
        assert app._loading_widget._base_status == "Retrying"

        gate.set()
        assert await wait_until(pilot, lambda: not app._agent_job_active())

        assert len(app.query(ErrorMessage)) == 0
        assert len(app.query(SlashCommandMessage)) == 0
        assert app.query_one(AssistantMessage).get_content() == "partial recovered."


@pytest.mark.asyncio
async def test_interrupting_auto_retry_keeps_event_listener_alive() -> None:
    retry_gate = asyncio.Event()

    class GatedRetryBackend(FakeBackend):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.calls = 0
            self.retry_started = asyncio.Event()

        async def complete_streaming(self, **kwargs):
            self.calls += 1
            async for chunk in super().complete_streaming(**kwargs):
                yield chunk
                if self.calls == 2:
                    self.retry_started.set()
                    await retry_gate.wait()

    backend = GatedRetryBackend([
        [mock_llm_chunk(content="partial", stop_reason=None)],
        [mock_llm_chunk(content=" retry", stop_reason=None)],
        [mock_llm_chunk(content="after cancel")],
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    try:
        async with app.run_test() as pilot:
            await pilot.pause(0.1)
            chat_input = app.query_one(ChatInputContainer)
            chat_input.post_message(ChatInputContainer.Submitted("first"))
            assert await wait_until(pilot, backend.retry_started.is_set)

            await pilot.press("escape")
            assert await wait_until(pilot, lambda: not app._agent_job_active())

            chat_input.post_message(ChatInputContainer.Submitted("next"))
            assert await wait_until(pilot, lambda: len(backend.requests_messages) == 3)
            assert await wait_until(
                pilot,
                lambda: any(
                    "after cancel" in message.get_content()
                    for message in app.query(AssistantMessage)
                ),
            )
    finally:
        retry_gate.set()


@pytest.mark.asyncio
async def test_empty_incomplete_stream_retries_original_request() -> None:
    backend = FakeBackend([[], [mock_llm_chunk(content="Recovered")]])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("hi"))
        assert await wait_until(pilot, lambda: len(backend.requests_messages) == 2)
        assert await wait_until(pilot, lambda: not app._agent_job_active())

        assert [message.get_content() for message in app.query(AssistantMessage)] == [
            "Recovered"
        ]
        assert len(app.query(ErrorMessage)) == 0

    assert all(
        message.role is not Role.assistant for message in backend.requests_messages[-1]
    )


@pytest.mark.asyncio
async def test_incomplete_stream_stops_after_two_automatic_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capture = MagicMock()
    monkeypatch.setattr("vibe.cli.textual_ui.app.capture_sentry_exception", capture)
    backend = FakeBackend([
        [mock_llm_chunk(content="first", stop_reason=None)],
        [mock_llm_chunk(content=" second", stop_reason=None)],
        [mock_llm_chunk(content=" third", stop_reason=None)],
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("hi"))
        assert await wait_until(pilot, lambda: len(backend.requests_messages) == 3)
        assert await wait_until(pilot, lambda: not app._agent_job_active())
        await pilot.pause(0.1)

        assert len(backend.requests_messages) == 3
        assert len(app.query(ErrorMessage)) == 1
        assert "/retry" in str(app.query_one(ErrorMessage)._error)

    # The silent retries stay out of Sentry, but the exhausted case -- a
    # persistent regression -- must still be reported exactly once.
    assert capture.call_count == 1


@pytest.mark.asyncio
async def test_incomplete_stream_does_not_retry_ahead_of_queued_prompts() -> None:
    class GatedBackend(FakeBackend):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.calls = 0
            self.started = [asyncio.Event() for _ in range(3)]
            self.release = [asyncio.Event() for _ in range(3)]

        async def complete_streaming(self, **kwargs):
            idx = self.calls
            self.calls += 1
            self.started[idx].set()
            if idx in (0, 1):
                await self.release[idx].wait()
            async for chunk in super().complete_streaming(**kwargs):
                yield chunk

    backend = GatedBackend([
        [mock_llm_chunk(content="t0 done")],
        [mock_llm_chunk(content="t1 partial", stop_reason=None)],
        [mock_llm_chunk(content="t2 done")],
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    def server_queue_len() -> int:
        return len(app.app_server.turn_queue.items)

    async with app.run_test() as pilot:
        await pilot.pause()
        chat_input = app.query_one(ChatInputContainer)

        # t0 runs; t1 queues behind it. When t0 finishes, t1 promotes and runs.
        chat_input.post_message(ChatInputContainer.Submitted("t0"))
        assert await wait_until(pilot, backend.started[0].is_set)
        chat_input.post_message(ChatInputContainer.Submitted("t1"))
        # Wait for the server to durably accept t1, not just the client's optimistic
        # queue projection (len(app._queue) is set before the enqueue RPC lands).
        assert await wait_until(
            pilot, lambda: len(app._queue) == 1 and server_queue_len() == 1
        )

        # Release t0 and wait until the client has processed t1's promotion: its
        # queue block is cleared and the turn is running. Only then is a follow-up
        # prompt guaranteed to become a *separate* turn behind t1 rather than being
        # folded into t1's about-to-promote merged block.
        backend.release[0].set()
        assert await wait_until(
            pilot,
            lambda: (
                backend.started[1].is_set()
                and app.app_server.turn_active
                and len(app._queue) == 0
            ),
        )

        # t2 queues only after t1 has started, so it is a separate turn behind the
        # turn that returns an incomplete stream. Wait until it is durably queued
        # both client- and server-side before releasing t1, so the incomplete-stream
        # retry decision sees an authoritative queue rather than an in-flight enqueue.
        chat_input.post_message(ChatInputContainer.Submitted("t2"))
        assert await wait_until(
            pilot, lambda: len(app._queue) == 1 and server_queue_len() == 1
        )

        # t1 returns incomplete while t2 is queued: the auto-retry must defer to
        # the queued prompt rather than retry ahead of it.
        backend.release[1].set()
        assert await wait_until(
            pilot, lambda: not app._agent_job_active() and len(app._queue) == 0
        )
        assert await wait_until(
            pilot,
            lambda: any(
                "t2 done" in message.get_content()
                for message in app.query(AssistantMessage)
            ),
        )
        assert await wait_until(pilot, lambda: len(app.query(ErrorMessage)) == 1)
        assert await wait_until(
            pilot,
            lambda: (
                app.query_one(ContextProgress).tokens.current_tokens
                == agent_loop.stats.context_tokens
            ),
        )

        contents = " ".join(m.get_content() for m in app.query(AssistantMessage))
        assert "t2 done" in contents
        assert len(app.query(ErrorMessage)) == 1
        assert "/retry" in str(app.query_one(ErrorMessage)._error)


@pytest.mark.asyncio
async def test_retry_command_reuses_interrupted_assistant_message() -> None:
    backend = FakeInterruptedStreamingBackend([
        [mock_llm_chunk(content="Ran three dummy read-only")],
        [mock_llm_chunk(content=" tool calls successfully.")],
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("hi"))
        await _wait_for_retry_error(app, pilot)
        assert await wait_until(pilot, lambda: not app._agent_job_active())

        chat_input.post_message(
            ChatInputContainer.Submitted("/retry Keep the conclusion concise.")
        )
        assert await wait_until(
            pilot,
            lambda: (
                agent_loop.messages[-1].role is Role.assistant
                and agent_loop.messages[-1].content == " tool calls successfully."
            ),
        )
        assert await wait_until(
            pilot,
            lambda: (
                len(app.query(AssistantMessage)) == 1
                and app.query_one(AssistantMessage).get_content()
                == "Ran three dummy read-only tool calls successfully."
            ),
        )

        visible_turn = [
            widget
            for widget in app._messages_area.children
            if isinstance(widget, AssistantMessage | ErrorMessage | SlashCommandMessage)
        ]
        assert [type(widget) for widget in visible_turn] == [AssistantMessage]
        assert [
            message._content
            for message in app.query(UserMessage)
            if not isinstance(message, SlashCommandMessage)
        ] == ["hi"]

    assert backend.streaming_attempts == 2
    retry_message = backend.requests_messages[-1][-1]
    assert retry_message.injected is True
    assert retry_message.content is not None
    assert retry_message.content.startswith(f"<{VIBE_WARNING_TAG}>")
    assert "without repeating text already produced" in retry_message.content
    assert "additional instructions from the user" in retry_message.content
    assert "Keep the conclusion concise." in retry_message.content


@pytest.mark.asyncio
async def test_retry_command_keeps_separate_assistant_after_reasoning() -> None:
    backend = FakeInterruptedStreamingBackend([
        [mock_llm_chunk(content="partial")],
        [
            mock_llm_chunk(content="", reasoning_content="thinking"),
            mock_llm_chunk(content="recovered"),
        ],
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("hi"))
        await _wait_for_retry_error(app, pilot)
        assert await wait_until(pilot, lambda: not app._agent_job_active())

        chat_input.post_message(ChatInputContainer.Submitted("/retry"))
        assert await wait_until(
            pilot,
            lambda: (
                agent_loop.messages[-1].role is Role.assistant
                and agent_loop.messages[-1].content == "recovered"
            ),
        )
        assert await wait_until(pilot, lambda: len(app.query(AssistantMessage)) == 2)

        assert [message.get_content() for message in app.query(AssistantMessage)] == [
            "partial",
            "recovered",
        ]
        assert len(app.query(ReasoningMessage)) == 1
        assert len(app.query(ErrorMessage)) == 0
        assert len(app.query(SlashCommandMessage)) == 0


@pytest.mark.asyncio
async def test_retry_command_keeps_diagnostics_until_retry_progress() -> None:
    backend = FakeInterruptedStreamingBackend([[mock_llm_chunk(content="partial")]])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("hi"))
        await _wait_for_retry_error(app, pilot)
        assert await wait_until(pilot, lambda: not app._agent_job_active())

        backend._exception_to_raise = RuntimeError("retry failed")
        chat_input.post_message(ChatInputContainer.Submitted("/retry"))
        assert await wait_until(pilot, lambda: backend.streaming_attempts == 2)
        assert await wait_until(pilot, lambda: not app._agent_job_active())
        assert await wait_until(pilot, lambda: len(app.query(ErrorMessage)) == 2)

        assert [message.get_content() for message in app.query(AssistantMessage)] == [
            "partial"
        ]
        assert len(app.query(ErrorMessage)) == 2
        assert len(app.query(SlashCommandMessage)) == 1
