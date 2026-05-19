from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from textual import events

from vibe.cli.textual_ui.widgets.question_app import QuestionApp
from vibe.core.tools.builtins.ask_user_question import (
    AskUserQuestionArgs,
    Choice,
    Question,
)

_TEST_GRACE_PERIOD_S = 0.5


@pytest.fixture
def question_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.question_app._INPUT_GRACE_PERIOD_S",
        _TEST_GRACE_PERIOD_S,
    )
    args = AskUserQuestionArgs(
        questions=[
            Question(
                question="Pick one",
                header="Pick",
                options=[Choice(label="A"), Choice(label="B")],
            )
        ]
    )
    app = QuestionApp(args)
    app._mount_time = 100.0
    return app


class TestQuestionAppGracePeriod:
    def test_select_and_cancel_ignored_within_grace_period(
        self, question_app: QuestionApp
    ):
        with (
            patch("vibe.cli.textual_ui.widgets.question_app.time") as mock_time,
            patch.object(question_app, "post_message") as posted,
        ):
            mock_time.monotonic.return_value = 100.0 + _TEST_GRACE_PERIOD_S - 0.01
            assert question_app.is_within_grace_period()

            question_app.action_select()
            question_app.action_cancel()

            posted.assert_not_called()

    def test_cancel_posts_message_after_grace_period(self, question_app: QuestionApp):
        with (
            patch("vibe.cli.textual_ui.widgets.question_app.time") as mock_time,
            patch.object(question_app, "post_message") as posted,
        ):
            mock_time.monotonic.return_value = 100.0 + _TEST_GRACE_PERIOD_S + 0.01

            question_app.action_cancel()

            posted.assert_called_once()
            assert isinstance(posted.call_args.args[0], QuestionApp.Cancelled)

    def test_navigation_works_during_grace_period(self, question_app: QuestionApp):
        with patch("vibe.cli.textual_ui.widgets.question_app.time") as mock_time:
            mock_time.monotonic.return_value = 100.0 + 0.01
            assert question_app.is_within_grace_period()

            assert question_app.selected_option == 0
            question_app.action_move_down()
            assert question_app.selected_option == 1
            question_app.action_move_up()
            assert question_app.selected_option == 0

    def test_number_key_consumed_but_not_acted_within_grace_period(
        self, question_app: QuestionApp
    ):
        with (
            patch("vibe.cli.textual_ui.widgets.question_app.time") as mock_time,
            patch.object(question_app, "post_message") as posted,
        ):
            mock_time.monotonic.return_value = 100.0 + _TEST_GRACE_PERIOD_S - 0.01
            event = MagicMock(spec=events.Key)
            event.character = "1"

            handled = question_app._handle_number_key(event)

            assert handled is True
            event.stop.assert_called_once()
            event.prevent_default.assert_called_once()
            posted.assert_not_called()

    def test_number_key_selects_option_after_grace_period(
        self, question_app: QuestionApp
    ):
        with (
            patch("vibe.cli.textual_ui.widgets.question_app.time") as mock_time,
            patch.object(question_app, "post_message") as posted,
        ):
            mock_time.monotonic.return_value = 100.0 + _TEST_GRACE_PERIOD_S + 0.01
            event = MagicMock(spec=events.Key)
            event.character = "1"

            handled = question_app._handle_number_key(event)

            assert handled is True
            assert question_app.selected_option == 0
            posted.assert_called_once()
            assert isinstance(posted.call_args.args[0], QuestionApp.Answered)
