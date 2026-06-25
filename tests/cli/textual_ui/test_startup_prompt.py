from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from tests.conftest import build_test_vibe_app
from vibe.cli.plan_offer.decide_plan_offer import PlanInfo
from vibe.cli.plan_offer.ports.whoami_gateway import WhoAmIPlanType
from vibe.cli.textual_ui.widgets.session_picker import SessionPickerApp


@pytest.mark.asyncio
async def test_startup_prompt_waits_for_startup_resume_picker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(initial_prompt="continue the work")
    app._show_resume_picker = True
    process_prompt = Mock()

    monkeypatch.setattr(app, "_resolve_plan", AsyncMock())
    monkeypatch.setattr(app, "_check_and_show_whats_new", AsyncMock())
    monkeypatch.setattr(app, "_schedule_update_notification", Mock())
    monkeypatch.setattr(app, "_process_initial_prompt", process_prompt)

    await app._complete_post_ready_startup()

    process_prompt.assert_not_called()


@pytest.mark.asyncio
async def test_startup_prompt_runs_after_startup_resume_picker_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(initial_prompt="continue the work")
    app._show_resume_picker = True
    app._startup_command_availability_ready.set()
    process_prompt = Mock()

    monkeypatch.setattr(app, "_switch_to_input_app", AsyncMock())
    monkeypatch.setattr(app, "_resume_local_session", AsyncMock())
    monkeypatch.setattr(app, "_process_initial_prompt", process_prompt)

    await app.on_session_picker_app_session_selected(
        SessionPickerApp.SessionSelected("local:session-1", "session-1")
    )

    assert app._show_resume_picker is False
    process_prompt.assert_called_once_with()


@pytest.mark.asyncio
async def test_startup_teleport_waits_for_plan_resolution_after_session_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = build_test_vibe_app(initial_prompt="continue the work")
    app._show_resume_picker = True
    app._teleport_on_start = True
    run_worker = Mock()
    handle_teleport = Mock(return_value=object())
    handle_user_message = Mock(return_value=object())

    monkeypatch.setattr(app, "_switch_to_input_app", AsyncMock())
    monkeypatch.setattr(app, "_resume_local_session", AsyncMock())
    monkeypatch.setattr(app, "run_worker", run_worker)
    monkeypatch.setattr(app, "_handle_teleport_command", handle_teleport)
    monkeypatch.setattr(app, "_handle_user_message", handle_user_message)
    monkeypatch.setattr(app.commands, "has_command", lambda name: name == "teleport")

    task = asyncio.create_task(
        app.on_session_picker_app_session_selected(
            SessionPickerApp.SessionSelected("local:session-1", "session-1")
        )
    )
    await asyncio.sleep(0)

    handle_teleport.assert_not_called()
    handle_user_message.assert_not_called()

    app._plan_info = PlanInfo(WhoAmIPlanType.CHAT)
    app._refresh_command_registry()
    app._startup_command_availability_ready.set()
    await task

    handle_teleport.assert_called_once_with("continue the work")
    handle_user_message.assert_not_called()
    run_worker.assert_called_once_with(handle_teleport.return_value, exclusive=False)
