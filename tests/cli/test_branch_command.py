from __future__ import annotations

import asyncio
from pathlib import Path
import time

import pytest

from tests.conftest import (
    build_test_agent_loop,
    build_test_vibe_app,
    build_test_vibe_config,
)
from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.app_server.models import IdleSessionStatus, PublicSession, PublicSessionState
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
    SessionForkResponse,
)
from vibe.cli.textual_ui.widgets.branch_created_message import BranchCreatedMessage
from vibe.cli.textual_ui.widgets.chat_input.container import ChatInputContainer
from vibe.cli.textual_ui.widgets.messages import ErrorMessage
from vibe.core.config import SessionLoggingConfig


def _enabled_session_config(save_dir: Path) -> SessionLoggingConfig:
    return SessionLoggingConfig(enabled=True, save_dir=str(save_dir))


def _fork_response(new_session_id: str, source_session_id: str) -> SessionForkResponse:
    return SessionForkResponse(
        source_session_id=source_session_id,
        state=PublicSessionState(
            event_id=2,
            session=PublicSession(
                id=new_session_id,
                status=IdleSessionStatus(),
                created_at=1,
                updated_at=1,
            ),
        ),
        last_event_id=2,
    )


def _build_app(tmp_path: Path):
    config = build_test_vibe_config(session_logging=_enabled_session_config(tmp_path))
    agent_loop = build_test_agent_loop(config=config)
    return build_test_vibe_app(agent_loop=agent_loop)


class _BlockingBackend(FakeBackend):
    def __init__(self) -> None:
        super().__init__([[mock_llm_chunk(content="done")]] * 4)
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def complete(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            self.started.set()
            await self.release.wait()
        return await super().complete(**kwargs)


def _blocked_app(tmp_path: Path):
    backend = _BlockingBackend()
    config = build_test_vibe_config(session_logging=_enabled_session_config(tmp_path))
    agent_loop = build_test_agent_loop(config=config, backend=backend)
    return build_test_vibe_app(agent_loop=agent_loop), backend


async def _wait_until(pilot, predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await pilot.pause(0.05)
    return False


@pytest.mark.asyncio
async def test_branch_forks_latest_and_shows_resume_hint(tmp_path: Path) -> None:
    app = _build_app(tmp_path)
    captured: dict[str, object] = {}

    async with app.run_test() as pilot:
        sessions = app.app_server.resources.sessions

        async def recording_fork(entry_id=None, *, attach=True):
            captured["entry_id"] = entry_id
            captured["attach"] = attach
            return _fork_response("new-branch-session-id", app.app_server.session_id)

        sessions.fork = recording_fork  # type: ignore[method-assign]
        old_session_id = app.app_server.session_id

        handled = await app._handle_command("/branch")
        await pilot.pause()

        assert handled is True
        assert captured == {"entry_id": None, "attach": False}

        branch_messages = app.query(BranchCreatedMessage)
        assert len(branch_messages) == 1
        content = branch_messages[0].get_content()
        assert "new-bran" in content
        assert "vibe --resume new-bran" in content
        # The current session is left unchanged (no rebind, no widget teardown).
        assert app.app_server.session_id == old_session_id


@pytest.mark.asyncio
async def test_branch_surfaces_error_when_session_logging_disabled(
    tmp_path: Path,
) -> None:
    app = _build_app(tmp_path)

    async with app.run_test() as pilot:
        sessions = app.app_server.resources.sessions

        async def failing_fork(entry_id=None, *, attach=True):
            raise AppServerResponseError(
                ProtocolError(
                    code=ProtocolErrorCode.CONFLICT,
                    message="Detached forks require session logging to be enabled",
                )
            )

        sessions.fork = failing_fork  # type: ignore[method-assign]
        old_session_id = app.app_server.session_id

        handled = await app._handle_command("/branch")
        await pilot.pause()

        assert handled is True

        errors = app.query(ErrorMessage)
        assert any(
            "Failed to branch session" in str(error._error)
            and "Detached forks require session logging to be enabled"
            in str(error._error)
            for error in errors
        )
        assert len(app.query(BranchCreatedMessage)) == 0
        # Current session is unchanged on failure.
        assert app.app_server.session_id == old_session_id


@pytest.mark.asyncio
async def test_branch_surfaces_non_appserver_errors(tmp_path: Path) -> None:
    # The handler catches Exception broadly (not just AppServerResponseError) so
    # connection errors and wire-validation failures surface cleanly too.
    app = _build_app(tmp_path)

    async with app.run_test() as pilot:
        sessions = app.app_server.resources.sessions

        async def failing_fork(entry_id=None, *, attach=True):
            raise ConnectionError("app server is unreachable")

        sessions.fork = failing_fork  # type: ignore[method-assign]
        old_session_id = app.app_server.session_id

        handled = await app._handle_command("/branch")
        await pilot.pause()

        assert handled is True

        errors = app.query(ErrorMessage)
        assert any(
            "Failed to branch session" in str(error._error)
            and "app server is unreachable" in str(error._error)
            for error in errors
        )
        assert len(app.query(BranchCreatedMessage)) == 0
        assert app.app_server.session_id == old_session_id


@pytest.mark.asyncio
async def test_branch_is_rejected_while_agent_turn_active(tmp_path: Path) -> None:
    # /branch is side_channel=False, so the main-queue dispatcher rejects it
    # while a turn is active (tested at the dispatch level, not the handler
    # level — the handler is never called while busy).
    app, backend = _blocked_app(tmp_path)
    called: dict[str, bool] = {}

    async with app.run_test() as pilot:
        await app._session_ready.wait()
        sessions = app.app_server.resources.sessions

        async def must_not_run(entry_id=None, *, attach=True):
            called["fork_called"] = True
            return _fork_response("should-not-happen", app.app_server.session_id)

        sessions.fork = must_not_run  # type: ignore[method-assign]

        chat_input = app.query_one(ChatInputContainer)
        chat_input.post_message(ChatInputContainer.Submitted("block queue"))
        assert await _wait_until(pilot, backend.started.is_set)
        # The blocking prompt is promoted to the active turn, so the client
        # queue drains to empty before the slash command is submitted.
        assert await _wait_until(pilot, lambda: not app._queue)

        chat_input.post_message(ChatInputContainer.Submitted("/branch"))

        assert await _wait_until(
            pilot,
            lambda: any(
                "Slash commands cannot be queued" in notification.message
                for notification in app._notifications
            ),
        )
        # Input is preserved for retry when the session returns to idle.
        assert chat_input.value == "/branch"
        assert not app._queue
        # Give any stray handler invocation a chance to surface.
        await asyncio.sleep(0)
        await pilot.pause()
        assert "fork_called" not in called
        assert len(app.query(BranchCreatedMessage)) == 0
        backend.release.set()
