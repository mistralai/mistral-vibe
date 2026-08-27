from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from vibe.app_server.models import WorkspaceTrustDetails
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
    assert opened.showed_trust_prompt is True
    assert opened.showed_resume_picker is False
    assert calls == ["trust_status", "trust_decision", "open_session"]


@pytest.mark.asyncio
async def test_resume_picker_opens_one_fresh_session() -> None:
    # With --resume, startup opens a single fresh session and defers session
    # selection to AppVibe's in-app picker: it must not resume here, and must not
    # open more than one runtime.
    host = MagicMock()
    host.cwd = "/workspace"
    session = MagicMock()
    host.start_session = AsyncMock(return_value=session)
    host.resume_session = AsyncMock()
    host.open_session = AsyncMock()

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
    host.open_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_continue_opens_fresh_session_with_auto_continue_descriptor() -> None:
    # --continue opens a fresh session via start_session (not the cold open_session)
    # and sets continue_latest so AppVibe drives the resume in-app.
    host = MagicMock()
    host.cwd = "/workspace"
    session = MagicMock()
    host.start_session = AsyncMock(return_value=session)
    host.resume_session = AsyncMock()
    host.open_session = AsyncMock()

    opened = await startup.open_textual_session(
        host,
        prompt_for_workspace_trust=False,
        show_resume_picker=False,
        initially_resuming=True,
    )

    assert opened is not None and not opened.resumed
    assert opened.session is session
    assert opened.continue_latest is True
    assert opened.resume_session_id is None
    host.start_session.assert_awaited_once_with()
    host.resume_session.assert_not_awaited()
    host.open_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_resume_with_id_opens_fresh_session_with_auto_resume_descriptor() -> None:
    # --resume <id> opens a fresh session via start_session (not the cold
    # open_session) and sets resume_session_id so AppVibe resumes in-app.
    host = MagicMock()
    host.cwd = "/workspace"
    session = MagicMock()
    host.start_session = AsyncMock(return_value=session)
    host.resume_session = AsyncMock()
    host.open_session = AsyncMock()

    opened = await startup.open_textual_session(
        host,
        prompt_for_workspace_trust=False,
        show_resume_picker=False,
        initially_resuming=True,
        resume_session_id="target-session-id",
    )

    assert opened is not None and not opened.resumed
    assert opened.session is session
    assert opened.resume_session_id == "target-session-id"
    assert opened.continue_latest is False
    host.start_session.assert_awaited_once_with()
    host.resume_session.assert_not_awaited()
    host.open_session.assert_not_awaited()
