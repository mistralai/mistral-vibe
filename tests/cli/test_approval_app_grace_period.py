from __future__ import annotations

from unittest.mock import patch

import pytest
from textual import events
from textual.app import App, ComposeResult

from tests.stubs.app_config import build_test_app_config
from vibe.app_server.models import (
    EffectCallDisplay,
    PathGrantScope,
    ShellEffectDetail,
    ShellEffectInput,
)
from vibe.cli.textual_ui.widgets.approval_app import ApprovalApp
from vibe.permissions import PermissionScope, RequiredPermission

_TEST_GRACE_PERIOD_S = 0.5


def _shell_approval(reason: str | None) -> ApprovalApp:
    return ApprovalApp(
        effect=ShellEffectDetail(
            tool_name="bash",
            input=ShellEffectInput(command="rm -rf build"),
            display=EffectCallDisplay(summary="bash", status_text="Running"),
        ),
        config=build_test_app_config(),
        reason=reason,
    )


@pytest.fixture
def approval_app(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "vibe.cli.textual_ui.widgets.approval_app._INPUT_GRACE_PERIOD_S",
        _TEST_GRACE_PERIOD_S,
    )
    app = ApprovalApp(
        effect=ShellEffectDetail(
            tool_name="bash",
            input=ShellEffectInput(command="echo hello"),
            display=EffectCallDisplay(summary="bash", status_text="Running"),
        ),
        config=build_test_app_config(),
    )
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


class TestVimKeybindings:
    def test_j_moves_down(self, approval_app: ApprovalApp):
        with patch.object(approval_app, "_update_options"):
            assert approval_app.selected_option == 0

            approval_app.on_key(events.Key("j", "j"))

            assert approval_app.selected_option == 1

    def test_k_moves_up(self, approval_app: ApprovalApp):
        with patch.object(approval_app, "_update_options"):
            assert approval_app.selected_option == 0

            approval_app.on_key(events.Key("k", "k"))

            # Wraps to last option (index 3)
            assert approval_app.selected_option == 3

    def test_vim_navigation_works_during_grace_period(self, approval_app: ApprovalApp):
        with (
            patch("vibe.cli.textual_ui.widgets.approval_app.time") as mock_time,
            patch.object(approval_app, "_update_options"),
        ):
            mock_time.monotonic.return_value = 100.0 + 0.01
            assert approval_app.is_within_grace_period()

            assert approval_app.selected_option == 0
            approval_app.on_key(events.Key("j", "j"))
            assert approval_app.selected_option == 1
            approval_app.on_key(events.Key("k", "k"))
            assert approval_app.selected_option == 0


class TestReasonRendering:
    @pytest.mark.asyncio
    async def test_reason_is_rendered_as_a_warning_line(self) -> None:
        approval = _shell_approval("deletes the build directory")

        class _Harness(App[None]):
            def compose(self) -> ComposeResult:
                yield approval

        async with _Harness().run_test() as pilot:
            await pilot.pause()

            reason_widget = approval.query_one(".approval-reason")
            assert "deletes the build directory" in str(reason_widget.render())

    @pytest.mark.asyncio
    async def test_no_reason_line_when_reason_is_absent(self) -> None:
        approval = _shell_approval(None)

        class _Harness(App[None]):
            def compose(self) -> ComposeResult:
                yield approval

        async with _Harness().run_test() as pilot:
            await pilot.pause()

            assert not approval.query(".approval-reason")


class TestPathScopeOptions:
    @pytest.fixture
    def scoped_approval(self) -> ApprovalApp:
        return ApprovalApp(
            effect=ShellEffectDetail(
                tool_name="bash",
                input=ShellEffectInput(command="cat /outside/config.json"),
                display=EffectCallDisplay(summary="bash", status_text="Running"),
            ),
            config=build_test_app_config(),
            required_permissions=[
                RequiredPermission(
                    scope=PermissionScope.OUTSIDE_DIRECTORY,
                    invocation_pattern="/outside/config.json",
                    session_pattern="vibe-path:exact:/outside/config.json",
                    label="outside workdir (/outside/config.json)",
                )
            ],
            path_scope_choices=[PathGrantScope.EXACT],
        )

    def test_builds_exact_file_session_and_permanent_choices(
        self, scoped_approval: ApprovalApp
    ) -> None:
        assert [option[0] for option in scoped_approval.options] == [
            "Allow once",
            "Allow this file only for this session",
            "Always allow this file",
            "Deny",
        ]

    def test_builds_recursive_folder_session_and_permanent_choices(
        self, scoped_approval: ApprovalApp
    ) -> None:
        scoped_approval.path_scope_choices = [PathGrantScope.DIRECTORY_RECURSIVE]

        assert [option[0] for option in scoped_approval._build_options()] == [
            "Allow once",
            "Allow this folder for this session",
            "Always allow this folder",
            "Deny",
        ]

    def test_builds_plural_recursive_choices_for_multiple_targets(
        self, scoped_approval: ApprovalApp
    ) -> None:
        scoped_approval.required_permissions.append(
            RequiredPermission(
                scope=PermissionScope.OUTSIDE_DIRECTORY,
                invocation_pattern="/var/log",
                session_pattern="vibe-path:exact:/var/log",
                label="outside workdir (/var/log)",
                path_scope_root="/var/log",
            )
        )
        scoped_approval.path_scope_choices = [PathGrantScope.DIRECTORY_RECURSIVE]

        assert [option[0] for option in scoped_approval._build_options()] == [
            "Allow once",
            "Allow these folders for this session",
            "Always allow these folders",
            "Deny",
        ]

    def test_number_four_always_selects_deny(
        self, scoped_approval: ApprovalApp
    ) -> None:
        scoped_approval.path_scope_choices = list(PathGrantScope)
        scoped_approval.options = scoped_approval._build_options()

        with patch.object(scoped_approval, "post_message") as posted:
            scoped_approval.action_select_4()

        assert isinstance(posted.call_args.args[0], ApprovalApp.ApprovalRejected)

    def test_scoped_choices_post_the_selected_typed_scope(
        self, scoped_approval: ApprovalApp
    ) -> None:
        with patch.object(scoped_approval, "post_message") as posted:
            scoped_approval.action_select_2()
            scoped_approval.action_select_3()
            scoped_approval.action_reject()

        session_message = posted.call_args_list[0].args[0]
        permanent_message = posted.call_args_list[1].args[0]
        assert isinstance(session_message, ApprovalApp.ApprovalGrantedAlwaysTool)
        assert session_message.path_scope is PathGrantScope.EXACT
        assert isinstance(permanent_message, ApprovalApp.ApprovalGrantedAlwaysPermanent)
        assert permanent_message.path_scope is PathGrantScope.EXACT
        assert isinstance(
            posted.call_args_list[2].args[0], ApprovalApp.ApprovalRejected
        )
