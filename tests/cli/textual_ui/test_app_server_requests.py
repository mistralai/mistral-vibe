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
from vibe.app_server.models import (
    ApprovalCallbackDetail,
    EffectCallDisplay,
    GenericEffectDetail,
    OpenCallbackState,
    PublicCallbackEntry,
    PublicEntryGenerationStatus,
    QuestionChoice,
    UserAnswer,
    UserInputCallbackDetail,
    UserInputCallbackOutput,
    UserQuestion,
    UserQuestionRequest,
    UserQuestionResult,
    WorkspaceTrustDetails,
)
from vibe.app_server.protocol import WorkspaceTrustStatusResponse
from vibe.cli.textual_ui import startup
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.question_app import QuestionApp
from vibe.core.config import SessionLoggingConfig
from vibe.core.types import ScheduledLoop, UserMessageEvent
from vibe.setup.trusted_folders.trust_folder_dialog import TrustFolderApp


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
    assert await startup._resolve_workspace_trust(host)

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
