from __future__ import annotations

from unittest.mock import MagicMock, patch

from vibe.cli.textual_ui.widgets.feedback_bar import FeedbackBar


class TestFeedbackBarState:
    def test_show_activates(self):
        bar = FeedbackBar()
        bar.display = False
        bar._set_active = MagicMock()

        bar.show()

        bar._set_active.assert_called_once_with(True)

    def test_show_skips_when_already_displayed(self):
        bar = FeedbackBar()
        bar.display = True
        bar._set_active = MagicMock()

        bar.show()

        bar._set_active.assert_not_called()

    def test_hide_calls_set_active_false_when_displayed(self):
        bar = FeedbackBar()
        bar.display = True
        bar._set_active = MagicMock()

        bar.hide()

        bar._set_active.assert_called_once_with(False)

    def test_hide_does_nothing_when_already_hidden(self):
        bar = FeedbackBar()
        bar.display = False
        bar._set_active = MagicMock()

        bar.hide()

        bar._set_active.assert_not_called()

    def test_handle_feedback_key_posts_message_and_deactivates(self):
        bar = FeedbackBar()
        bar.set_timer = MagicMock()
        bar.post_message = MagicMock()
        bar.query_one = MagicMock()
        mock_text_area = MagicMock()
        mock_text_area.feedback_active = True
        mock_app = MagicMock()
        mock_app.query_one.return_value = mock_text_area

        with patch.object(
            type(bar), "app", new_callable=lambda: property(lambda self: mock_app)
        ):
            bar.handle_feedback_key(3)

        assert mock_text_area.feedback_active is False
        bar.post_message.assert_called_once()
        msg = bar.post_message.call_args[0][0]
        assert isinstance(msg, FeedbackBar.FeedbackGiven)
        assert msg.rating == 3
        bar.set_timer.assert_called_once()


class TestFeedbackGivenMessage:
    def test_message_stores_rating(self):
        msg = FeedbackBar.FeedbackGiven(rating=2)
        assert msg.rating == 2
