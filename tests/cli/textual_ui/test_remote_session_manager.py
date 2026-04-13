from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.cli.textual_ui.remote.remote_session_manager import RemoteSessionManager
from vibe.core.tools.builtins.ask_user_question import AskUserQuestionArgs
from vibe.core.types import WaitingForInputEvent


@pytest.fixture
def manager() -> RemoteSessionManager:
    return RemoteSessionManager()


class TestProperties:
    def test_is_active_false_by_default(self, manager: RemoteSessionManager) -> None:
        assert manager.is_active is False

    def test_is_terminated_false_when_inactive(
        self, manager: RemoteSessionManager
    ) -> None:
        assert manager.is_terminated is False

    def test_is_waiting_for_input_false_when_inactive(
        self, manager: RemoteSessionManager
    ) -> None:
        assert manager.is_waiting_for_input is False

    def test_has_pending_input_false_by_default(
        self, manager: RemoteSessionManager
    ) -> None:
        assert manager.has_pending_input is False

    def test_session_id_none_when_inactive(self, manager: RemoteSessionManager) -> None:
        assert manager.session_id is None


class TestAttachDetach:
    @pytest.mark.asyncio
    async def test_attach_activates_manager(
        self, manager: RemoteSessionManager
    ) -> None:
        with patch(
            "vibe.cli.textual_ui.remote.remote_session_manager.RemoteEventsSource"
        ) as MockSource:
            mock_source = MagicMock()
            mock_source.session_id = "test-session-id"
            MockSource.return_value = mock_source

            config = MagicMock()
            await manager.attach(session_id="test-session-id", config=config)

            assert manager.is_active is True
            assert manager.session_id == "test-session-id"

    @pytest.mark.asyncio
    async def test_detach_cleans_up(self, manager: RemoteSessionManager) -> None:
        with patch(
            "vibe.cli.textual_ui.remote.remote_session_manager.RemoteEventsSource"
        ) as MockSource:
            mock_source = AsyncMock()
            mock_source.session_id = "test-id"
            MockSource.return_value = mock_source

            config = MagicMock()
            await manager.attach(session_id="test-id", config=config)
            await manager.detach()

            assert manager.is_active is False
            assert manager.session_id is None
            assert manager.has_pending_input is False
            mock_source.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_attach_detaches_previous_session(
        self, manager: RemoteSessionManager
    ) -> None:
        with patch(
            "vibe.cli.textual_ui.remote.remote_session_manager.RemoteEventsSource"
        ) as MockSource:
            first_source = AsyncMock()
            second_source = MagicMock()
            second_source.session_id = "second-id"
            MockSource.side_effect = [first_source, second_source]

            config = MagicMock()
            await manager.attach(session_id="first-id", config=config)
            await manager.attach(session_id="second-id", config=config)

            first_source.close.assert_called_once()
            assert manager.session_id == "second-id"


class TestValidateInput:
    @pytest.mark.asyncio
    async def test_returns_none_when_waiting_for_input(
        self, manager: RemoteSessionManager
    ) -> None:
        with patch(
            "vibe.cli.textual_ui.remote.remote_session_manager.RemoteEventsSource"
        ) as MockSource:
            mock_source = MagicMock()
            mock_source.is_terminated = False
            mock_source.is_waiting_for_input = True
            MockSource.return_value = mock_source

            config = MagicMock()
            await manager.attach(session_id="id", config=config)

            assert manager.validate_input() is None

    @pytest.mark.asyncio
    async def test_returns_warning_when_terminated(
        self, manager: RemoteSessionManager
    ) -> None:
        with patch(
            "vibe.cli.textual_ui.remote.remote_session_manager.RemoteEventsSource"
        ) as MockSource:
            mock_source = MagicMock()
            mock_source.is_terminated = True
            MockSource.return_value = mock_source

            config = MagicMock()
            await manager.attach(session_id="id", config=config)

            result = manager.validate_input()
            assert result is not None
            assert "ended" in result

    @pytest.mark.asyncio
    async def test_returns_warning_when_not_waiting_for_input(
        self, manager: RemoteSessionManager
    ) -> None:
        with patch(
            "vibe.cli.textual_ui.remote.remote_session_manager.RemoteEventsSource"
        ) as MockSource:
            mock_source = MagicMock()
            mock_source.is_terminated = False
            mock_source.is_waiting_for_input = False
            MockSource.return_value = mock_source

            config = MagicMock()
            await manager.attach(session_id="id", config=config)

            result = manager.validate_input()
            assert result is not None
            assert "not waiting" in result


class TestSendPrompt:
    @pytest.mark.asyncio
    async def test_raises_when_inactive_and_required(
        self, manager: RemoteSessionManager
    ) -> None:
        with pytest.raises(RuntimeError, match="No active remote session"):
            await manager.send_prompt("hello")

    @pytest.mark.asyncio
    async def test_returns_silently_when_inactive_and_not_required(
        self, manager: RemoteSessionManager
    ) -> None:
        await manager.send_prompt("hello", require_source=False)

    @pytest.mark.asyncio
    async def test_restores_pending_on_error(
        self, manager: RemoteSessionManager
    ) -> None:
        with patch(
            "vibe.cli.textual_ui.remote.remote_session_manager.RemoteEventsSource"
        ) as MockSource:
            mock_source = AsyncMock()
            mock_source.send_prompt.side_effect = Exception("connection error")
            MockSource.return_value = mock_source

            config = MagicMock()
            await manager.attach(session_id="id", config=config)

            event = WaitingForInputEvent(task_id="t1", label="test")
            manager.set_pending_input(event)

            with pytest.raises(Exception, match="connection error"):
                await manager.send_prompt("hello")

            assert manager.has_pending_input is True


class TestPendingInput:
    def test_set_and_cancel_pending_input(self, manager: RemoteSessionManager) -> None:
        event = WaitingForInputEvent(task_id="t1", label="test")
        manager.set_pending_input(event)
        assert manager.has_pending_input is True

        manager.cancel_pending_input()
        assert manager.has_pending_input is False


class TestBuildQuestionArgs:
    def test_returns_none_with_no_predefined_answers(
        self, manager: RemoteSessionManager
    ) -> None:
        event = WaitingForInputEvent(task_id="t1", label="test")
        assert manager.build_question_args(event) is None

    def test_returns_none_with_one_predefined_answer(
        self, manager: RemoteSessionManager
    ) -> None:
        event = WaitingForInputEvent(
            task_id="t1", label="test", predefined_answers=["only one"]
        )
        assert manager.build_question_args(event) is None

    def test_returns_args_with_two_predefined_answers(
        self, manager: RemoteSessionManager
    ) -> None:
        event = WaitingForInputEvent(
            task_id="t1", label="Pick one", predefined_answers=["yes", "no"]
        )
        result = manager.build_question_args(event)
        assert result is not None
        assert isinstance(result, AskUserQuestionArgs)
        assert len(result.questions) == 1
        assert result.questions[0].question == "Pick one"
        assert len(result.questions[0].options) == 2

    def test_caps_at_four_predefined_answers(
        self, manager: RemoteSessionManager
    ) -> None:
        event = WaitingForInputEvent(
            task_id="t1",
            label="Pick",
            predefined_answers=["a", "b", "c", "d", "e", "f"],
        )
        result = manager.build_question_args(event)
        assert result is not None
        assert len(result.questions[0].options) == 4

    def test_uses_default_question_when_no_label(
        self, manager: RemoteSessionManager
    ) -> None:
        event = WaitingForInputEvent(task_id="t1", predefined_answers=["a", "b"])
        result = manager.build_question_args(event)
        assert result is not None
        assert result.questions[0].question == "Choose an answer"


class TestBuildTerminalMessage:
    def test_completed_when_no_source(self, manager: RemoteSessionManager) -> None:
        msg_type, text = manager.build_terminal_message()
        assert msg_type == "info"
        assert "completed" in text

    @pytest.mark.asyncio
    async def test_failed_state(self, manager: RemoteSessionManager) -> None:
        with patch(
            "vibe.cli.textual_ui.remote.remote_session_manager.RemoteEventsSource"
        ) as MockSource:
            mock_source = MagicMock()
            mock_source.is_failed = True
            mock_source.is_canceled = False
            MockSource.return_value = mock_source

            config = MagicMock()
            await manager.attach(session_id="id", config=config)

            msg_type, text = manager.build_terminal_message()
            assert msg_type == "error"
            assert "failed" in text.lower()

    @pytest.mark.asyncio
    async def test_canceled_state(self, manager: RemoteSessionManager) -> None:
        with patch(
            "vibe.cli.textual_ui.remote.remote_session_manager.RemoteEventsSource"
        ) as MockSource:
            mock_source = MagicMock()
            mock_source.is_failed = False
            mock_source.is_canceled = True
            MockSource.return_value = mock_source

            config = MagicMock()
            await manager.attach(session_id="id", config=config)

            msg_type, text = manager.build_terminal_message()
            assert msg_type == "warning"
            assert "canceled" in text.lower()
