from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_app
from vibe.app_server.models import AgentStatsSnapshot, PreparedPrompt
from vibe.app_server.protocol import (
    AppServerResponseError,
    ProtocolError,
    ProtocolErrorCode,
)
from vibe.app_server.session import AppServerSession
from vibe.cli.textual_ui.app import VibeApp
from vibe.cli.textual_ui.widgets.chat_input import ChatInputContainer
from vibe.cli.textual_ui.widgets.context_progress import ContextProgress
from vibe.cli.textual_ui.widgets.messages import ErrorMessage

_RESUMED_TOKENS = 50_000
_RESUMED_CONTEXT_WINDOW = 200_000


@pytest.fixture
def vibe_app() -> VibeApp:
    return build_test_vibe_app()


def _app_with_fake_runtime(runtime: MagicMock) -> VibeApp:
    app = build_test_vibe_app()
    app_server = object.__new__(AppServerSession)
    app_server.resources = MagicMock()
    app_server.resources.runtime = runtime
    app._app_server = app_server
    return app


@pytest.mark.asyncio
async def test_finish_resume_notices_shows_notices_after_ready() -> None:
    runtime = MagicMock()
    runtime.wait_until_ready = AsyncMock()
    app = _app_with_fake_runtime(runtime)

    with (
        patch.object(app, "_show_post_init_notices_once", AsyncMock()) as notices,
        patch.object(app, "_mount_and_scroll", AsyncMock()) as mount,
        patch.object(app, "_refresh_banner", MagicMock()),
    ):
        await app._finish_resume_notices()

    runtime.wait_until_ready.assert_awaited_once()
    notices.assert_awaited_once()
    mount.assert_not_awaited()


@pytest.mark.asyncio
async def test_finish_resume_notices_defers_notices_until_ready() -> None:
    gate = asyncio.Event()

    async def _blocked() -> None:
        await gate.wait()

    runtime = MagicMock()
    runtime.wait_until_ready = AsyncMock(side_effect=_blocked)
    app = _app_with_fake_runtime(runtime)

    with (
        patch.object(app, "_show_post_init_notices_once", AsyncMock()) as notices,
        patch.object(app, "_mount_and_scroll", AsyncMock()),
        patch.object(app, "_refresh_banner", MagicMock()),
    ):
        task = asyncio.create_task(app._finish_resume_notices())
        await asyncio.sleep(0)
        notices.assert_not_awaited()
        gate.set()
        await task
        notices.assert_awaited_once()


@pytest.mark.asyncio
async def test_finish_resume_notices_surfaces_late_init_failure() -> None:
    runtime = MagicMock()
    runtime.wait_until_ready = AsyncMock(side_effect=RuntimeError("mcp boom"))
    app = _app_with_fake_runtime(runtime)

    with (
        patch.object(app, "_show_post_init_notices_once", AsyncMock()) as notices,
        patch.object(app, "_mount_and_scroll", AsyncMock()) as mount,
        patch.object(app, "_refresh_banner", MagicMock()),
    ):
        await app._finish_resume_notices()

    notices.assert_not_awaited()
    mount.assert_awaited_once()
    (message,), _ = mount.call_args
    assert isinstance(message, ErrorMessage)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "code", [ProtocolErrorCode.CONFLICT, ProtocolErrorCode.NOT_FOUND]
)
async def test_finish_resume_notices_is_quiet_when_superseded(
    code: ProtocolErrorCode,
) -> None:
    runtime = MagicMock()
    runtime.wait_until_ready = AsyncMock(
        side_effect=AppServerResponseError(
            ProtocolError(code=code, message="superseded by a newer resume")
        )
    )
    app = _app_with_fake_runtime(runtime)

    with (
        patch.object(app, "_show_post_init_notices_once", AsyncMock()) as notices,
        patch.object(app, "_mount_and_scroll", AsyncMock()) as mount,
        patch.object(app, "_refresh_banner", MagicMock()),
    ):
        await app._finish_resume_notices()

    notices.assert_not_awaited()
    mount.assert_not_awaited()
    assert app._post_init_notices_shown is False


@pytest.mark.asyncio
async def test_finish_resume_notices_noop_when_already_shown() -> None:
    runtime = MagicMock()
    runtime.wait_until_ready = AsyncMock()
    app = _app_with_fake_runtime(runtime)
    app._post_init_notices_shown = True

    with (
        patch.object(app, "_show_post_init_notices_once", AsyncMock()) as notices,
        patch.object(app, "_mount_and_scroll", AsyncMock()),
        patch.object(app, "_refresh_banner", MagicMock()),
    ):
        await app._finish_resume_notices()

    runtime.wait_until_ready.assert_not_awaited()
    notices.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_local_session_updates_context_progress(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        runtime = vibe_app.app_server.resources.runtime
        assert runtime.stats.context_tokens == 0

        def _apply_resumed_stats(*args: object) -> None:
            runtime._state.stats = AgentStatsSnapshot(
                context_tokens=_RESUMED_TOKENS, session_prompt_tokens=_RESUMED_TOKENS
            )
            runtime._state.context_window = _RESUMED_CONTEXT_WINDOW

        vibe_app.app_server.resume = AsyncMock(side_effect=_apply_resumed_stats)
        vibe_app._resume_history_from_messages = AsyncMock()
        vibe_app._mount_and_scroll = AsyncMock()

        await vibe_app._resume_local_session("abcd1234")

        widget = vibe_app.query_one(ContextProgress)
        assert widget.tokens.current_tokens == _RESUMED_TOKENS
        assert widget.tokens.max_tokens == _RESUMED_CONTEXT_WINDOW


@pytest.mark.asyncio
async def test_resume_local_session_shows_zero_when_no_llm_activity(
    vibe_app: VibeApp,
) -> None:
    async with vibe_app.run_test():
        vibe_app.app_server.resume = AsyncMock()
        vibe_app._resume_history_from_messages = AsyncMock()
        vibe_app._mount_and_scroll = AsyncMock()

        await vibe_app._resume_local_session("abcd1234")

        widget = vibe_app.query_one(ContextProgress)
        assert widget.tokens.current_tokens == 0


@pytest.mark.asyncio
async def test_enqueue_prompt_prepares_eagerly(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test():
        prepared = PreparedPrompt(display_text="hello", prompt_text="hello-prepared")
        prepare = AsyncMock(return_value=prepared)
        with patch.object(vibe_app, "_prepare_prompt_or_abort", prepare):
            result = await vibe_app._enqueue_prompt_with_resources("hello")

        assert result is True
        prepare.assert_awaited_once_with("hello")
        items = vibe_app._queue.queue.items
        assert len(items) == 1
        assert items[0].prepared_prompt is prepared


@pytest.mark.asyncio
async def test_submit_during_fresh_bootstrap_waits_then_dispatches() -> None:
    release = asyncio.Event()
    app = build_test_vibe_app()
    app._mount_first = True
    original_starter = app._start_app_server
    assert original_starter is not None

    async def _latched_starter() -> AppServerSession:
        await release.wait()
        return await original_starter()

    app._start_app_server = _latched_starter

    turn = AsyncMock()
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        assert app._app_server is None
        assert not app._session_ready.is_set()

        with patch.object(app, "_handle_turn", turn):
            submit = asyncio.create_task(app._handle_user_message("hello"))
            await asyncio.sleep(0.05)
            # Blocked on _session_ready inside _prepare_prompt_or_abort — no
            # RuntimeError from the unbound app_server property.
            assert not submit.done()
            assert app._loading_widget is not None
            turn.assert_not_awaited()

            release.set()
            await pilot.pause(0.3)
            await submit
            if app._agent_task is not None:
                await app._agent_task

        assert app._session_ready.is_set()
        turn.assert_awaited_once()


async def _assert_submit_during_resume_window_dispatches(
    app: VibeApp, *, resume_session_id: str | None, continue_latest: bool
) -> None:
    async with app.run_test(size=(120, 40)):
        # Warm start pre-sets ready; clear it and re-arm the resume flags to
        # simulate the mount-first auto-resume window.
        app._session_ready.clear()
        app._resume_session_id = resume_session_id
        app._continue_latest = continue_latest

        gate = asyncio.Event()

        async def _blocked_resume(_session_id: str) -> None:
            await gate.wait()

        turn = AsyncMock()
        with (
            patch.object(
                app.app_server.resources.sessions,
                "resolve_continue_session",
                AsyncMock(return_value="abcd1234"),
            ),
            patch.object(
                app, "_resume_local_session", AsyncMock(side_effect=_blocked_resume)
            ),
            patch.object(app, "_process_startup_prompt_when_available", AsyncMock()),
            patch.object(app, "_handle_turn", turn),
        ):
            resume_task = asyncio.create_task(app._auto_resume_on_startup())
            await asyncio.sleep(0.05)
            assert not app._session_ready.is_set()

            submit = asyncio.create_task(app._handle_user_message("go"))
            await asyncio.sleep(0.05)
            assert not submit.done()
            turn.assert_not_awaited()

            gate.set()
            await resume_task
            await submit
            if app._agent_task is not None:
                await app._agent_task

        assert app._session_ready.is_set()
        turn.assert_awaited_once()


@pytest.mark.asyncio
async def test_submit_during_resume_window_dispatches(vibe_app: VibeApp) -> None:
    await _assert_submit_during_resume_window_dispatches(
        vibe_app, resume_session_id="abcd1234", continue_latest=False
    )


@pytest.mark.asyncio
async def test_submit_during_continue_window_dispatches(vibe_app: VibeApp) -> None:
    await _assert_submit_during_resume_window_dispatches(
        vibe_app, resume_session_id=None, continue_latest=True
    )


@pytest.mark.asyncio
async def test_session_ready_set_on_fresh_cold_start() -> None:
    app = build_test_vibe_app()
    app._mount_first = True
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        assert app._app_server is not None
        assert app._session_ready.is_set()


@pytest.mark.asyncio
async def test_session_ready_set_on_warm_start(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        assert vibe_app._session_ready.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("resume_session_id", "continue_latest", "continue_target"),
    [("abcd1234", False, "abcd1234"), (None, True, "abcd1234"), (None, True, None)],
    ids=["resume", "continue", "no-sessions-found"],
)
async def test_session_ready_set_after_auto_resume(
    vibe_app: VibeApp,
    resume_session_id: str | None,
    continue_latest: bool,
    continue_target: str | None,
) -> None:
    async with vibe_app.run_test(size=(120, 40)):
        vibe_app._session_ready.clear()
        vibe_app._resume_session_id = resume_session_id
        vibe_app._continue_latest = continue_latest
        with (
            patch.object(
                vibe_app.app_server.resources.sessions,
                "resolve_continue_session",
                AsyncMock(return_value=continue_target),
            ),
            patch.object(vibe_app, "_resume_local_session", AsyncMock()),
            patch.object(
                vibe_app, "_process_startup_prompt_when_available", AsyncMock()
            ),
            patch.object(
                vibe_app,
                "_show_custom_tools_deprecation_warning_after_initial_history",
                AsyncMock(),
            ),
        ):
            await vibe_app._auto_resume_on_startup()
        assert vibe_app._session_ready.is_set()


@pytest.mark.asyncio
async def test_session_ready_set_on_picker_selected_success(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test(size=(120, 40)):
        vibe_app._session_ready.clear()
        event = MagicMock(session_id="abcd1234")
        with (
            patch.object(vibe_app, "_switch_to_input_app", AsyncMock()),
            patch.object(vibe_app, "_resume_local_session", AsyncMock()),
        ):
            await vibe_app.on_session_picker_app_session_selected(event)
        assert vibe_app._session_ready.is_set()


@pytest.mark.asyncio
async def test_session_ready_set_on_picker_selected_failure(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test(size=(120, 40)):
        vibe_app._session_ready.clear()
        event = MagicMock(session_id="abcd1234")
        with (
            patch.object(vibe_app, "_switch_to_input_app", AsyncMock()),
            patch.object(
                vibe_app,
                "_resume_local_session",
                AsyncMock(side_effect=RuntimeError("boom")),
            ),
            patch.object(vibe_app, "_mount_and_scroll", AsyncMock()),
            patch.object(
                vibe_app, "_rebuild_transcript_from_current_session", AsyncMock()
            ),
        ):
            await vibe_app.on_session_picker_app_session_selected(event)
        assert vibe_app._session_ready.is_set()


@pytest.mark.asyncio
async def test_session_ready_set_on_picker_cancelled(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test(size=(120, 40)):
        vibe_app._session_ready.clear()
        event = MagicMock()
        with (
            patch.object(vibe_app, "_switch_to_input_app", AsyncMock()),
            patch.object(vibe_app, "_mount_and_scroll", AsyncMock()),
        ):
            await vibe_app.on_session_picker_app_cancelled(event)
        assert vibe_app._session_ready.is_set()


@pytest.mark.asyncio
async def test_session_ready_set_on_picker_delete_last_exit(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test(size=(120, 40)):
        vibe_app._session_ready.clear()
        with patch.object(vibe_app, "_switch_to_input_app", AsyncMock()):
            await vibe_app._exit_picker_to_input()
        assert vibe_app._session_ready.is_set()


@pytest.mark.asyncio
async def test_session_ready_set_on_picker_zero_sessions(vibe_app: VibeApp) -> None:
    async with vibe_app.run_test(size=(120, 40)):
        vibe_app._session_ready.clear()
        with (
            patch.object(vibe_app, "_switch_from_input", AsyncMock()),
            patch.object(vibe_app, "_switch_to_input_app", AsyncMock()),
            patch.object(vibe_app, "_mount_and_scroll", AsyncMock()),
            patch.object(
                vibe_app.app_server.resources.sessions,
                "list",
                AsyncMock(return_value=[]),
            ),
        ):
            await vibe_app._show_session_picker()
        assert vibe_app._session_ready.is_set()


@pytest.mark.asyncio
async def test_bootstrap_error_disables_input_and_never_deadlocks() -> None:
    app = build_test_vibe_app()
    app._mount_first = True

    async def _failing_starter() -> AppServerSession:
        raise RuntimeError("session start failed")

    app._start_app_server = _failing_starter

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.2)
        assert app._app_server is None
        assert app._fatal_init_error is True
        # Bootstrap error never marks ready — the input is disabled, so no
        # submit can reach the turn path to deadlock on the await.
        assert not app._session_ready.is_set()
        container = app.query_one(ChatInputContainer)
        assert container.disabled is True


@pytest.mark.asyncio
async def test_ensure_runtime_ready_blocks_until_session_ready() -> None:
    runtime = MagicMock()
    runtime.ready = True
    runtime.wait_until_ready = AsyncMock()
    app = _app_with_fake_runtime(runtime)
    app._session_ready.clear()

    task = asyncio.create_task(app._ensure_runtime_ready())
    await asyncio.sleep(0.05)
    assert not task.done()
    runtime.wait_until_ready.assert_not_awaited()

    app._mark_session_ready()
    await task
    runtime.wait_until_ready.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_runtime_ready_instant_when_session_ready() -> None:
    runtime = MagicMock()
    runtime.ready = True
    runtime.wait_until_ready = AsyncMock()
    app = _app_with_fake_runtime(runtime)
    app._mark_session_ready()

    await asyncio.wait_for(app._ensure_runtime_ready(), timeout=1.0)
    runtime.wait_until_ready.assert_awaited_once()
