from __future__ import annotations

from unittest.mock import patch

from pydantic import BaseModel
import pytest

from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.core.config import VibeConfig

_TEST_GRACE_PERIOD_S = 0.5


class FakeArgs(BaseModel):
    command: str = "echo hello"


@pytest.fixture
def approval_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.approval_app._INPUT_GRACE_PERIOD_S",
        _TEST_GRACE_PERIOD_S,
    )
    config = VibeConfig()
    app = ApprovalApp(tool_name="bash", tool_args=FakeArgs(), config=config)
    app._mount_time = 100.0
    return app


class TestGracePeriod:
    def test_actions_ignored_within_grace_period(self, approval_app: ApprovalApp):
        with (
            patch("vibe.cli.textual_ui.widgets.approval_app.time") as mock_time,
            patch.object(approval_app, "post_message") as posted,
        ):
            mock_time.monotonic.return_value = 100.0 + _TEST_GRACE_PERIOD_S - 0.01
            assert approval_app.is_within_grace_period()

            approval_app.action_select()
            approval_app.action_select_1()
            approval_app.action_select_2()
            approval_app.action_select_3()
            approval_app.action_reject()

            posted.assert_not_called()

    def test_actions_post_messages_after_grace_period(self, approval_app: ApprovalApp):
        with (
            patch("vibe.cli.textual_ui.widgets.approval_app.time") as mock_time,
            patch.object(approval_app, "post_message") as posted,
        ):
            mock_time.monotonic.return_value = 100.0 + _TEST_GRACE_PERIOD_S + 0.01
            assert not approval_app.is_within_grace_period()

            approval_app.action_select_1()
            approval_app.action_reject()

            assert posted.call_count == 2
            assert isinstance(
                posted.call_args_list[0].args[0], ApprovalApp.ApprovalGranted
            )
            assert isinstance(
                posted.call_args_list[1].args[0], ApprovalApp.ApprovalRejected
            )

    def test_arrow_keys_work_during_grace_period(self, approval_app: ApprovalApp):
        with (
            patch("vibe.cli.textual_ui.widgets.approval_app.time") as mock_time,
            patch.object(approval_app, "_update_options"),
        ):
            mock_time.monotonic.return_value = 100.0 + 0.01
            assert approval_app.is_within_grace_period()

            assert approval_app.selected_option == 0
            approval_app.action_move_down()
            assert approval_app.selected_option == 1
            approval_app.action_move_up()
            assert approval_app.selected_option == 0
