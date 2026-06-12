from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import build_test_vibe_app, build_test_vibe_config
from vibe.cli.textual_ui.widgets.messages import ErrorMessage, UserCommandMessage
from vibe.cli.textual_ui.widgets.session_picker import SessionPickerApp
from vibe.core.config import SessionLoggingConfig
from vibe.core.session.resume_sessions import short_session_id


def _enabled_session_config(save_dir: Path) -> SessionLoggingConfig:
    return SessionLoggingConfig(enabled=True, save_dir=str(save_dir))


class FakeSessionPicker:
    def __init__(self, *, has_sessions_after_remove: bool = True) -> None:
        self.has_sessions = True
        self.removed_option_ids: list[str] = []
        self.cleared_pending_option_ids: list[str] = []
        self._has_sessions_after_remove = has_sessions_after_remove

    def remove_session(self, option_id: str) -> bool:
        self.removed_option_ids.append(option_id)
        self.has_sessions = self._has_sessions_after_remove
        return True

    def clear_pending_delete(self, option_id: str) -> bool:
        self.cleared_pending_option_ids.append(option_id)
        return True


@pytest.mark.asyncio
async def test_session_delete_request_deletes_local_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=_enabled_session_config(tmp_path), enable_connectors=False
    )
    app = build_test_vibe_app(config=config)
    picker = FakeSessionPicker()
    mounted_widgets: list[object] = []
    deleted_sessions: list[tuple[str, SessionLoggingConfig]] = []

    async def delete_session(
        session_id: str, session_config: SessionLoggingConfig
    ) -> None:
        deleted_sessions.append((session_id, session_config))

    async def mount_and_scroll(widget: object, after: object | None = None) -> None:
        mounted_widgets.append(widget)

    monkeypatch.setattr("vibe.cli.textual_ui.app.delete_saved_session", delete_session)
    monkeypatch.setattr(app, "query_one", lambda _selector: picker)
    monkeypatch.setattr(app, "_mount_and_scroll", mount_and_scroll)

    await app.on_session_picker_app_session_delete_requested(
        SessionPickerApp.SessionDeleteRequested(
            "local:deleted-session", "local", "deleted-session"
        )
    )

    assert deleted_sessions == [("deleted-session", config.session_logging)]
    assert picker.removed_option_ids == ["local:deleted-session"]
    assert any(
        isinstance(widget, UserCommandMessage)
        and widget._content
        == f"Deleted session `{short_session_id('deleted-session')}`."
        for widget in mounted_widgets
    )


@pytest.mark.asyncio
async def test_session_delete_request_keeps_picker_on_delete_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=_enabled_session_config(tmp_path), enable_connectors=False
    )
    app = build_test_vibe_app(config=config)
    picker = FakeSessionPicker()
    mounted_widgets: list[object] = []

    async def delete_session(
        session_id: str, session_config: SessionLoggingConfig
    ) -> None:
        raise RuntimeError("disk said no")

    async def mount_and_scroll(widget: object, after: object | None = None) -> None:
        mounted_widgets.append(widget)

    monkeypatch.setattr("vibe.cli.textual_ui.app.delete_saved_session", delete_session)
    monkeypatch.setattr(app, "query_one", lambda _selector: picker)
    monkeypatch.setattr(app, "_mount_and_scroll", mount_and_scroll)

    await app.on_session_picker_app_session_delete_requested(
        SessionPickerApp.SessionDeleteRequested(
            "local:deleted-session", "local", "deleted-session"
        )
    )

    assert picker.removed_option_ids == []
    assert picker.cleared_pending_option_ids == ["local:deleted-session"]
    assert any(
        isinstance(widget, ErrorMessage)
        and widget._error == "Failed to delete session: disk said no"
        for widget in mounted_widgets
    )


@pytest.mark.asyncio
async def test_session_delete_request_returns_to_input_when_picker_becomes_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=_enabled_session_config(tmp_path), enable_connectors=False
    )
    app = build_test_vibe_app(config=config)
    picker = FakeSessionPicker(has_sessions_after_remove=False)
    mounted_widgets: list[object] = []
    switched_to_input = False

    async def delete_session(
        session_id: str, session_config: SessionLoggingConfig
    ) -> None:
        pass

    async def mount_and_scroll(widget: object, after: object | None = None) -> None:
        mounted_widgets.append(widget)

    async def switch_to_input() -> None:
        nonlocal switched_to_input
        switched_to_input = True

    monkeypatch.setattr("vibe.cli.textual_ui.app.delete_saved_session", delete_session)
    monkeypatch.setattr(app, "query_one", lambda _selector: picker)
    monkeypatch.setattr(app, "_mount_and_scroll", mount_and_scroll)
    monkeypatch.setattr(app, "_switch_to_input_app", switch_to_input)

    await app.on_session_picker_app_session_delete_requested(
        SessionPickerApp.SessionDeleteRequested(
            "local:deleted-session", "local", "deleted-session"
        )
    )

    assert switched_to_input is True
    assert picker.removed_option_ids == ["local:deleted-session"]
    assert [
        widget._content
        for widget in mounted_widgets
        if isinstance(widget, UserCommandMessage)
    ] == [
        f"Deleted session `{short_session_id('deleted-session')}`.",
        "No saved sessions left for this directory.",
    ]


@pytest.mark.asyncio
async def test_session_delete_request_rejects_remote_sessions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=_enabled_session_config(tmp_path), enable_connectors=False
    )
    app = build_test_vibe_app(config=config)
    mounted_widgets: list[object] = []

    async def delete_session(
        session_id: str, session_config: SessionLoggingConfig
    ) -> None:
        pytest.fail("remote sessions should not be deleted")

    async def mount_and_scroll(widget: object, after: object | None = None) -> None:
        mounted_widgets.append(widget)

    monkeypatch.setattr("vibe.cli.textual_ui.app.delete_saved_session", delete_session)
    monkeypatch.setattr(app, "_mount_and_scroll", mount_and_scroll)

    await app.on_session_picker_app_session_delete_requested(
        SessionPickerApp.SessionDeleteRequested(
            "remote:remote-session", "remote", "remote-session"
        )
    )

    assert any(
        isinstance(widget, ErrorMessage)
        and widget._error == "Deleting remote sessions is not supported."
        for widget in mounted_widgets
    )


@pytest.mark.asyncio
async def test_session_delete_request_rejects_current_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = build_test_vibe_config(
        session_logging=_enabled_session_config(tmp_path), enable_connectors=False
    )
    app = build_test_vibe_app(config=config)
    picker = FakeSessionPicker()
    mounted_widgets: list[object] = []
    deleted_sessions: list[str] = []

    async def delete_session(
        session_id: str, session_config: SessionLoggingConfig
    ) -> None:
        deleted_sessions.append(session_id)

    async def mount_and_scroll(widget: object, after: object | None = None) -> None:
        mounted_widgets.append(widget)

    monkeypatch.setattr("vibe.cli.textual_ui.app.delete_saved_session", delete_session)
    monkeypatch.setattr(app, "query_one", lambda _selector: picker)
    monkeypatch.setattr(app, "_mount_and_scroll", mount_and_scroll)

    await app.on_session_picker_app_session_delete_requested(
        SessionPickerApp.SessionDeleteRequested(
            f"local:{app.agent_loop.session_id}", "local", app.agent_loop.session_id
        )
    )

    assert deleted_sessions == []
    assert picker.cleared_pending_option_ids == [f"local:{app.agent_loop.session_id}"]
    assert any(
        isinstance(widget, ErrorMessage)
        and widget._error == "Deleting the current session is not supported."
        for widget in mounted_widgets
    )
