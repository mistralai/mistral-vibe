from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.app_server.models import SavedSessionSummary, WorkspaceTrustDetails
from vibe.app_server.protocol import WorkspaceTrustStatusResponse
from vibe.cli.textual_ui import startup
from vibe.setup.trusted_folders.trust_folder_dialog import TrustFolderApp


@pytest.mark.asyncio
async def test_trust_is_resolved_before_session_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    host = MagicMock()
    host.cwd = "/workspace"
    host.trust_status = AsyncMock(
        side_effect=lambda cwd: (
            calls.append("trust_status")
            or WorkspaceTrustStatusResponse(
                status="untrusted",
                details=WorkspaceTrustDetails(
                    cwd=cwd,
                    detected_files=["AGENTS.md"],
                    settings_path="/home/user/.vibe/trusted_folders.toml",
                    available_decisions=["trust_cwd", "decline"],
                ),
            )
        )
    )
    host.decide_trust = AsyncMock(
        side_effect=lambda *args, **kwargs: (
            calls.append("trust_decision")
            or WorkspaceTrustStatusResponse(status="trusted")
        )
    )
    session = MagicMock()

    async def open_session():
        calls.append("open_session")
        return session

    host.open_session = open_session
    monkeypatch.setattr(
        TrustFolderApp, "run_trust_dialog_async", AsyncMock(return_value="trust_cwd")
    )

    opened = await startup.open_textual_session(
        host,
        prompt_for_workspace_trust=True,
        show_resume_picker=False,
        initially_resuming=False,
    )

    assert opened is not None
    assert opened.session is session
    assert calls == ["trust_status", "trust_decision", "open_session"]


@pytest.mark.asyncio
async def test_resume_picker_selects_session_before_opening_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = MagicMock()
    host.cwd = "/workspace"
    host.list_sessions = AsyncMock(
        return_value=[
            SavedSessionSummary(session_id="saved", cwd="/workspace", preview="Hi")
        ]
    )
    session = MagicMock()
    host.resume_session = AsyncMock(return_value=session)
    host.start_session = AsyncMock()
    monkeypatch.setattr(
        startup._StartupSessionPicker,
        "run_async",
        AsyncMock(return_value=startup._PickerResult(session_id="saved")),
    )

    opened = await startup.open_textual_session(
        host,
        prompt_for_workspace_trust=False,
        show_resume_picker=True,
        initially_resuming=False,
    )

    assert opened is not None and opened.resumed
    assert opened.session is session
    host.resume_session.assert_awaited_once_with("saved")
    host.start_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_picker_cancel_starts_one_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host = MagicMock()
    host.cwd = "/workspace"
    host.list_sessions = AsyncMock(
        return_value=[SavedSessionSummary(session_id="saved", cwd="/workspace")]
    )
    session = MagicMock()
    host.start_session = AsyncMock(return_value=session)
    host.resume_session = AsyncMock()
    monkeypatch.setattr(
        startup._StartupSessionPicker,
        "run_async",
        AsyncMock(return_value=startup._PickerResult()),
    )

    opened = await startup.open_textual_session(
        host,
        prompt_for_workspace_trust=False,
        show_resume_picker=True,
        initially_resuming=False,
    )

    assert opened is not None and not opened.resumed
    assert opened.session is session
    host.start_session.assert_awaited_once_with()
    host.resume_session.assert_not_awaited()
