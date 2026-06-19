from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from rich.text import Text
from textual.widgets import OptionList

from vibe.cli.textual_ui.widgets.session_picker import (
    SessionPickerApp,
    _format_relative_time,
)
from vibe.core.session.resume_sessions import ResumeSessionInfo


@pytest.fixture
def sample_sessions() -> list[ResumeSessionInfo]:
    return [
        ResumeSessionInfo(
            session_id="session-a",
            cwd="/test",
            title="Session A",
            end_time=(datetime.now(UTC) - timedelta(minutes=5)).isoformat(),
        ),
        ResumeSessionInfo(
            session_id="session-b",
            cwd="/test",
            title="Session B",
            end_time=(datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        ),
        ResumeSessionInfo(
            session_id="session-c",
            cwd="/test",
            title="Session C",
            end_time=(datetime.now(UTC) - timedelta(days=1)).isoformat(),
        ),
    ]


@pytest.fixture
def sample_latest_messages() -> dict[str, str]:
    return {
        "session-a": "Help me fix this bug",
        "session-b": "Refactor the authentication module",
        "session-c": "Add unit tests for the API",
    }


def assert_delete_state(picker: SessionPickerApp, *, kind: str, option_id: str) -> None:
    assert picker._delete_state is not None
    assert picker._delete_state.kind == kind
    assert picker._delete_state.option_id == option_id


class TestFormatRelativeTime:
    def test_just_now(self) -> None:
        now = datetime.now(UTC).isoformat()
        assert _format_relative_time(now) == "just now"

    def test_minutes_ago(self) -> None:
        time_5m_ago = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        assert _format_relative_time(time_5m_ago) == "5m ago"

    def test_hours_ago(self) -> None:
        time_2h_ago = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
        assert _format_relative_time(time_2h_ago) == "2h ago"

    def test_days_ago(self) -> None:
        time_3d_ago = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        assert _format_relative_time(time_3d_ago) == "3d ago"

    def test_weeks_ago(self) -> None:
        time_2w_ago = (datetime.now(UTC) - timedelta(weeks=2)).isoformat()
        assert _format_relative_time(time_2w_ago) == "2w ago"

    def test_none_returns_unknown(self) -> None:
        assert _format_relative_time(None) == "unknown"

    def test_invalid_format_returns_unknown(self) -> None:
        assert _format_relative_time("not-a-date") == "unknown"

    def test_handles_z_suffix(self) -> None:
        time_str = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        assert _format_relative_time(time_str) == "just now"

    def test_boundary_59_seconds(self) -> None:
        time_59s_ago = (datetime.now(UTC) - timedelta(seconds=59)).isoformat()
        assert _format_relative_time(time_59s_ago) == "just now"

    def test_boundary_60_seconds(self) -> None:
        time_60s_ago = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        assert _format_relative_time(time_60s_ago) == "1m ago"


class TestSessionPickerAppInit:
    def test_init_sets_properties(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        assert picker._sessions == sample_sessions
        assert picker._latest_messages == sample_latest_messages
        assert picker._current_session_id is None

    def test_id_is_sessionpicker_app(self) -> None:
        picker = SessionPickerApp(sessions=[], latest_messages={})
        assert picker.id == "sessionpicker-app"

    def test_can_focus_children_is_true(self) -> None:
        assert SessionPickerApp.can_focus_children is True

    def test_delete_confirmation_state_starts_empty(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        assert picker._delete_state is None

    def test_has_sessions_tracks_session_list(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        assert picker.has_sessions is True

        empty_picker = SessionPickerApp(sessions=[], latest_messages={})
        assert empty_picker.has_sessions is False


class TestSessionPickerMessages:
    def test_session_selected_stores_option_id(self) -> None:
        msg = SessionPickerApp.SessionSelected("test-session-id", "test-session-id")
        assert msg.option_id == "test-session-id"
        assert msg.session_id == "test-session-id"

    def test_cancelled_can_be_instantiated(self) -> None:
        msg = SessionPickerApp.Cancelled()
        assert isinstance(msg, SessionPickerApp.Cancelled)

    def test_session_delete_requested_stores_session_info(self) -> None:
        msg = SessionPickerApp.SessionDeleteRequested(
            "test-session-id", "test-session-id"
        )
        assert msg.option_id == "test-session-id"
        assert msg.session_id == "test-session-id"


class TestSessionPickerAppBindings:
    def _get_binding_keys(self) -> list[str]:
        keys = []
        for binding in SessionPickerApp.BINDINGS:
            if isinstance(binding, tuple) and len(binding) >= 1:
                keys.extend(binding[0].split(","))
            else:
                keys.extend(binding.key.split(","))
        return keys

    def test_has_escape_binding(self) -> None:
        assert "escape" in self._get_binding_keys()

    def test_has_delete_binding(self) -> None:
        assert "d" in self._get_binding_keys()
        assert "D" in self._get_binding_keys()


class TestSessionPickerSessionRemoval:
    def test_first_delete_request_enters_confirmation(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        option_list = FakeOptionList(highlighted_option_id="session-a")
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.action_request_delete()

        assert_delete_state(picker, kind="confirmation", option_id="session-a")
        assert option_list.replaced_prompts[-1].option_id == "session-a"
        assert (
            "Press D again to delete" in option_list.replaced_prompts[-1].prompt.plain
        )
        assert posted_messages == []

    def test_second_delete_request_posts_delete_message(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        option_list = FakeOptionList(highlighted_option_id="session-a")
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.action_request_delete()
        picker.action_request_delete()

        assert_delete_state(picker, kind="pending", option_id="session-a")
        assert option_list.replaced_prompts[-1].option_id == "session-a"
        assert "Deleting..." in option_list.replaced_prompts[-1].prompt.plain
        assert len(posted_messages) == 1
        message = posted_messages[0]
        assert isinstance(message, SessionPickerApp.SessionDeleteRequested)
        assert message.option_id == "session-a"
        assert message.session_id == "session-a"

    def test_delete_confirmation_is_consumed_after_request(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        option_list = FakeOptionList(highlighted_option_id="session-a")
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.action_request_delete()
        picker.action_request_delete()
        picker.action_request_delete()

        assert len(posted_messages) == 1
        assert_delete_state(picker, kind="pending", option_id="session-a")
        assert option_list.replaced_prompts[-1].option_id == "session-a"
        assert "Deleting..." in option_list.replaced_prompts[-1].prompt.plain

    def test_delete_request_shows_feedback_for_current_session(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions,
            latest_messages=sample_latest_messages,
            current_session_id="session-a",
        )
        option_list = FakeOptionList(highlighted_option_id="session-a")
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.action_request_delete()

        assert_delete_state(picker, kind="feedback", option_id="session-a")
        assert option_list.replaced_prompts[-1].option_id == "session-a"
        assert (
            "Can't delete current session"
            in option_list.replaced_prompts[-1].prompt.plain
        )
        assert posted_messages == []

        picker.action_request_delete()

        assert_delete_state(picker, kind="feedback", option_id="session-a")
        assert posted_messages == []

    def test_pending_delete_blocks_resume_selection(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        option_list = FakeOptionList(highlighted_option_id="session-a")
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.action_request_delete()
        picker.action_request_delete()
        picker.on_option_list_option_selected(
            cast(OptionList.OptionSelected, FakeOptionEvent("session-a"))
        )
        picker.on_option_list_option_selected(
            cast(OptionList.OptionSelected, FakeOptionEvent("session-b"))
        )

        assert len(posted_messages) == 1
        assert isinstance(posted_messages[0], SessionPickerApp.SessionDeleteRequested)
        assert_delete_state(picker, kind="pending", option_id="session-a")

    def test_clear_pending_delete_restores_session_option(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        option_list = FakeOptionList(highlighted_option_id="session-a")
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)

        picker.action_request_delete()
        picker.action_request_delete()

        assert picker.clear_pending_delete("session-a") is True
        assert picker._delete_state is None
        assert option_list.replaced_prompts[-1].option_id == "session-a"
        assert "Help me fix this bug" in option_list.replaced_prompts[-1].prompt.plain

    def test_highlighting_another_session_clears_confirmation(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        option_list = FakeOptionList(highlighted_option_id="session-a")
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        picker.action_request_delete()

        picker.on_option_list_option_highlighted(
            cast(OptionList.OptionHighlighted, FakeOptionEvent("session-b"))
        )

        assert picker._delete_state is None
        assert option_list.replaced_prompts[-1].option_id == "session-a"
        assert "Help me fix this bug" in option_list.replaced_prompts[-1].prompt.plain

    def test_highlighting_another_session_clears_delete_feedback(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions,
            latest_messages=sample_latest_messages,
            current_session_id="session-c",
        )
        option_list = FakeOptionList(highlighted_option_id="session-c")
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        picker.action_request_delete()

        picker.on_option_list_option_highlighted(
            cast(OptionList.OptionHighlighted, FakeOptionEvent("session-b"))
        )

        assert picker._delete_state is None
        assert option_list.replaced_prompts[-1].option_id == "session-c"
        assert (
            "Add unit tests for the API"
            in option_list.replaced_prompts[-1].prompt.plain
        )

    def test_escape_clears_confirmation_before_cancelling(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        option_list = FakeOptionList(highlighted_option_id="session-a")
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)
        picker.action_request_delete()

        picker.action_cancel()

        assert picker._delete_state is None
        assert posted_messages == []

        picker.action_cancel()

        assert len(posted_messages) == 1
        assert isinstance(posted_messages[0], SessionPickerApp.Cancelled)

    def test_escape_clears_delete_feedback_before_cancelling(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions,
            latest_messages=sample_latest_messages,
            current_session_id="session-c",
        )
        option_list = FakeOptionList(highlighted_option_id="session-c")
        posted_messages: list[object] = []
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        monkeypatch.setattr(picker, "post_message", posted_messages.append)
        picker.action_request_delete()

        picker.action_cancel()

        assert picker._delete_state is None
        assert posted_messages == []

        picker.action_cancel()

        assert len(posted_messages) == 1
        assert isinstance(posted_messages[0], SessionPickerApp.Cancelled)

    def test_remove_session_updates_picker_state(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        option_list = FakeOptionList(highlighted_option_id="session-a")
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)
        picker.action_request_delete()
        assert_delete_state(picker, kind="confirmation", option_id="session-a")

        assert picker.remove_session("session-a") is True

        assert [session.option_id for session in picker._sessions] == [
            "session-b",
            "session-c",
        ]
        assert "session-a" not in picker._latest_messages
        assert picker._delete_state is None
        assert option_list.removed_option_ids == ["session-a"]

    def test_remove_missing_session_returns_false(
        self,
        sample_sessions: list[ResumeSessionInfo],
        sample_latest_messages: dict[str, str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        picker = SessionPickerApp(
            sessions=sample_sessions, latest_messages=sample_latest_messages
        )
        option_list = FakeOptionList()
        monkeypatch.setattr(picker, "query_one", lambda _selector: option_list)

        assert picker.remove_session("missing") is False

        assert picker._sessions == sample_sessions
        assert picker._latest_messages == sample_latest_messages
        assert option_list.removed_option_ids == []


class FakeOption:
    def __init__(self, option_id: str) -> None:
        self.id = option_id


class FakeOptionEvent:
    def __init__(self, option_id: str) -> None:
        self.option = FakeOption(option_id)


class ReplacedPrompt:
    def __init__(self, option_id: str, prompt: Text) -> None:
        self.option_id = option_id
        self.prompt = prompt


class FakeOptionList:
    def __init__(self, highlighted_option_id: str | None = None) -> None:
        self.highlighted_option = (
            FakeOption(highlighted_option_id)
            if highlighted_option_id is not None
            else None
        )
        self.removed_option_ids: list[str] = []
        self.replaced_prompts: list[ReplacedPrompt] = []

    def remove_option(self, option_id: str) -> None:
        self.removed_option_ids.append(option_id)

    def replace_option_prompt(self, option_id: str, prompt: Text) -> None:
        self.replaced_prompts.append(ReplacedPrompt(option_id, prompt))
