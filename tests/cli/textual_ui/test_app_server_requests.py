from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
)
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend, FakeInterruptedStreamingBackend
from vibe.app_server.events import CallbackRequested
from vibe.app_server.models import (
    ApprovalCallbackDetail,
    ApprovalCallbackOutput,
    ApprovalDecision,
    ApprovalDecisionType,
    EffectCallDisplay,
    GenericEffectDetail,
    OpenCallbackState,
    PublicCallbackEntry,
    PublicEntryGenerationStatus,
    PublicError,
    QuestionChoice,
    TurnErrorCode,
    UserAnswer,
    UserInputCallbackDetail,
    UserInputCallbackOutput,
    UserQuestion,
    UserQuestionRequest,
    UserQuestionResult,
    WorkspaceTrustDetails,
)
from vibe.app_server.protocol import WorkspaceTrustStatusResponse
from vibe.app_server.session import AppServerTurnError
from vibe.cli.textual_ui import startup
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import (
    AssistantMessage,
    ErrorMessage,
    ReasoningMessage,
    SlashCommandMessage,
    UserMessage,
)
from vibe.cli.textual_ui.widgets.question_app import QuestionApp
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


async def _wait_until(pilot, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for UI state")
        await pilot.pause(0.01)


def _turn_error(code: TurnErrorCode) -> AppServerTurnError:
    return AppServerTurnError(PublicError(message="Network error", code=code))


async def _wait_for_retry_error(app: VibeApp, pilot) -> None:
    await _wait_until(
        pilot,
        lambda: any("/retry" in str(error._error) for error in app.query(ErrorMessage)),
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
    app._switch_to_approval_app.assert_awaited_once_with(effect, [])


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
        display=EffectCallDisplay(summary="example", status_text="Running"),
    )
    callback = _callback(ApprovalCallbackDetail(effect=effect))
    return callback.model_copy(
        update={"id": f"callback:{callback_id}", "callback_id": callback_id}
    )


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
        await _wait_until(pilot, lambda: app._app_server is not None)
        monkeypatch.setattr(app.app_server, "respond_to_callback", AsyncMock())

        await app._handle_turn_event(CallbackRequested(first))
        await _wait_until(pilot, lambda: app._active_callback is first)
        assert len(app.query(ApprovalApp)) == 1

        await app._handle_turn_event(CallbackRequested(second))
        await pilot.pause()
        assert list(app._pending_callbacks) == [second]
        assert len(app.query(ApprovalApp)) == 1

        await app._respond_to_active_callback(
            ApprovalCallbackOutput(
                decision=ApprovalDecision(type=ApprovalDecisionType.APPROVE)
            )
        )
        await _wait_until(pilot, lambda: app._active_callback is second)
        assert len(app.query(ApprovalApp)) == 1


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
        await _wait_until(pilot, lambda: app.app_server.turn_active)
        assert app._agent_task is None

        await pilot.press("escape")

        await asyncio.wait_for(interrupted.wait(), timeout=2)
        await _wait_until(pilot, lambda: not app.app_server.turn_active)


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
        await _wait_until(pilot, lambda: len(backend.requests_messages) == 2)
        await _wait_until(pilot, lambda: not app._agent_job_active())

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
        await _wait_until(pilot, backend.second_started.is_set)
        assert len(app.query(ErrorMessage)) == 0
        assert app._loading_widget is not None
        assert app._loading_widget._base_status == "Retrying"

        gate.set()
        await _wait_until(pilot, lambda: not app._agent_job_active())

        assert len(app.query(ErrorMessage)) == 0
        assert len(app.query(SlashCommandMessage)) == 0
        assert app.query_one(AssistantMessage).get_content() == "partial recovered."


@pytest.mark.asyncio
async def test_empty_incomplete_stream_retries_original_request() -> None:
    backend = FakeBackend([[], [mock_llm_chunk(content="Recovered")]])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("hi"))
        await _wait_until(pilot, lambda: len(backend.requests_messages) == 2)
        await _wait_until(pilot, lambda: not app._agent_job_active())

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
        await _wait_until(pilot, lambda: len(backend.requests_messages) == 3)
        await _wait_until(pilot, lambda: not app._agent_job_active())
        await pilot.pause(0.1)

        assert len(backend.requests_messages) == 3
        assert len(app.query(ErrorMessage)) == 1
        assert "/retry" in str(app.query_one(ErrorMessage)._error)

    # The silent retries stay out of Sentry, but the exhausted case -- a
    # persistent regression -- must still be reported exactly once.
    assert capture.call_count == 1


@pytest.mark.asyncio
async def test_auto_retry_does_not_drop_a_queued_prompt() -> None:
    # A turn started by the queue drain that auto-retries an incomplete stream
    # must not let the drain resume and start the next queued prompt
    # concurrently -- that would collide ("A turn is already running") and drop
    # the queued prompt. Gate turns 0 and 1 so the second prompt is queued only
    # once the drained turn is already in flight (otherwise the drain batches
    # both prompts into a single turn).
    class GatedBackend(FakeBackend):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.calls = 0
            self.started = [asyncio.Event() for _ in range(4)]
            self.release = {0: asyncio.Event(), 1: asyncio.Event()}

        async def complete_streaming(self, **kwargs):
            idx = self.calls
            self.calls += 1
            self.started[idx].set()
            if idx in self.release:
                await self.release[idx].wait()
            async for chunk in super().complete_streaming(**kwargs):
                yield chunk

    backend = GatedBackend([
        [mock_llm_chunk(content="t0 done")],
        [mock_llm_chunk(content="t1 partial", stop_reason=None)],
        [mock_llm_chunk(content=" t1 done")],
        [mock_llm_chunk(content="t2 done")],
    ])
    agent_loop = build_test_agent_loop(backend=backend, enable_streaming=True)
    app = build_test_vibe_app(agent_loop=agent_loop)

    async with app.run_test() as pilot:
        await pilot.pause(0.1)
        chat_input = app.query_one(ChatInputContainer)

        # Turn 0 runs directly and blocks; queue t1 behind it.
        chat_input.post_message(ChatInputContainer.Submitted("t0"))
        await _wait_until(pilot, backend.started[0].is_set)
        chat_input.post_message(ChatInputContainer.Submitted("t1"))
        await _wait_until(pilot, lambda: len(app._input_queue) == 1)

        # Release t0 -> drain starts t1 (alone). Once t1 is in flight, queue t2.
        backend.release[0].set()
        await _wait_until(pilot, backend.started[1].is_set)
        chat_input.post_message(ChatInputContainer.Submitted("t2"))
        await _wait_until(pilot, lambda: len(app._input_queue) == 1)

        # Let t1 fail (incomplete) and auto-retry, then drain t2.
        backend.release[1].set()
        await _wait_until(pilot, lambda: len(backend.requests_messages) == 4)
        await _wait_until(
            pilot, lambda: not app._agent_job_active() and len(app._input_queue) == 0
        )

        # t2 survived the auto-retry and ran to completion; nothing collided.
        contents = " ".join(m.get_content() for m in app.query(AssistantMessage))
        assert "t2 done" in contents
        assert len(app.query(ErrorMessage)) == 0


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
        await _wait_until(pilot, lambda: not app._agent_job_active())

        chat_input.post_message(
            ChatInputContainer.Submitted("/retry Keep the conclusion concise.")
        )
        await _wait_until(
            pilot,
            lambda: (
                agent_loop.messages[-1].role is Role.assistant
                and agent_loop.messages[-1].content == " tool calls successfully."
            ),
        )
        await _wait_until(
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
        await _wait_until(pilot, lambda: not app._agent_job_active())

        chat_input.post_message(ChatInputContainer.Submitted("/retry"))
        await _wait_until(
            pilot,
            lambda: (
                agent_loop.messages[-1].role is Role.assistant
                and agent_loop.messages[-1].content == "recovered"
            ),
        )
        await _wait_until(pilot, lambda: len(app.query(AssistantMessage)) == 2)

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
        await _wait_until(pilot, lambda: not app._agent_job_active())

        backend._exception_to_raise = RuntimeError("retry failed")
        chat_input.post_message(ChatInputContainer.Submitted("/retry"))
        await _wait_until(pilot, lambda: backend.streaming_attempts == 2)
        await _wait_until(pilot, lambda: not app._agent_job_active())
        await _wait_until(pilot, lambda: len(app.query(ErrorMessage)) == 2)

        assert [message.get_content() for message in app.query(AssistantMessage)] == [
            "partial"
        ]
        assert len(app.query(ErrorMessage)) == 2
        assert len(app.query(SlashCommandMessage)) == 1
