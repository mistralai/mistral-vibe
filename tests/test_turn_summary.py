from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging

import pytest

from tests.mock.utils import mock_llm_chunk
from tests.stubs.fake_backend import FakeBackend
from vibe.cli.turn_summary import (
    NARRATOR_MODEL,
    NoopTurnSummary,
    TurnSummaryResult,
    TurnSummaryTracker,
    create_narrator_backend,
)
from vibe.core.config import ModelConfig, ProviderConfig, VibeConfig
from vibe.core.llm.backend.mistral import MistralBackend
from vibe.core.types import AssistantEvent, Backend, ToolStreamEvent, UserMessageEvent

_TEST_MODEL = ModelConfig(name="test-model", provider="test", alias="test-model")


def _noop_callback(result: TurnSummaryResult) -> None:
    pass


class TestCreateNarratorBackend:
    def test_uses_mistral_provider(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        config = VibeConfig()
        result = create_narrator_backend(config)
        assert result is not None
        backend, model = result
        assert isinstance(backend, MistralBackend)
        assert model is NARRATOR_MODEL

    def test_uses_custom_provider_base_url(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        custom_provider = ProviderConfig(
            name="mistral",
            api_base="https://on-prem.example.com/v1",
            api_key_env_var="MISTRAL_API_KEY",
            backend=Backend.MISTRAL,
        )
        config = VibeConfig(providers=[custom_provider])
        result = create_narrator_backend(config)
        assert result is not None
        backend, model = result
        assert isinstance(backend, MistralBackend)
        assert backend._provider.api_base == custom_provider.api_base

    def test_returns_none_when_api_key_missing(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        config = VibeConfig()
        monkeypatch.delenv("MISTRAL_API_KEY")
        assert create_narrator_backend(config) is None

    def test_returns_none_when_provider_missing(self):
        config = VibeConfig(providers=[])
        assert create_narrator_backend(config) is None


class TestTrack:
    def _make_tracker(self, backend: FakeBackend | None = None) -> TurnSummaryTracker:
        return TurnSummaryTracker(
            backend=backend or FakeBackend(),
            model=_TEST_MODEL,
            on_summary=_noop_callback,
        )

    def test_assistant_event(self):
        tracker = self._make_tracker()
        tracker.start_turn("test")
        tracker.track(AssistantEvent(content="chunk1"))
        tracker.track(AssistantEvent(content="chunk2"))
        assert tracker._data is not None
        assert tracker._data.assistant_fragments == ["chunk1", "chunk2"]

    def test_assistant_event_empty_content_ignored(self):
        tracker = self._make_tracker()
        tracker.start_turn("test")
        tracker.track(AssistantEvent(content=""))
        assert tracker._data is not None
        assert tracker._data.assistant_fragments == []

    def test_start_turn_preserves_full_message(self):
        tracker = self._make_tracker()
        long_msg = "a" * 1500
        tracker.start_turn(long_msg)
        assert tracker._data is not None
        assert len(tracker._data.user_message) == 1500

    def test_start_turn_increments_generation(self):
        tracker = self._make_tracker()
        assert tracker.generation == 0
        tracker.start_turn("turn 1")
        assert tracker.generation == 1
        tracker.start_turn("turn 2")
        assert tracker.generation == 2

    def test_cancel_turn_clears_data(self):
        tracker = self._make_tracker()
        tracker.start_turn("test")
        assert tracker._data is not None
        tracker.cancel_turn()
        assert tracker._data is None

    def test_set_error_stores_message(self):
        tracker = self._make_tracker()
        tracker.start_turn("test")
        tracker.set_error("rate limit exceeded")
        assert tracker._data is not None
        assert tracker._data.error == "rate limit exceeded"

    def test_set_error_without_start_is_noop(self):
        tracker = self._make_tracker()
        tracker.set_error("should be ignored")
        assert tracker._data is None

    def test_cancel_turn_without_start_is_noop(self):
        tracker = self._make_tracker()
        tracker.cancel_turn()
        assert tracker._data is None

    def test_unrelated_events_ignored(self):
        tracker = self._make_tracker()
        tracker.start_turn("test")
        tracker.track(UserMessageEvent(content="hi", message_id="m1"))
        tracker.track(
            ToolStreamEvent(tool_name="bash", message="output", tool_call_id="tc1")
        )
        assert tracker._data is not None
        assert tracker._data.assistant_fragments == []


class TestTurnSummaryTracker:
    def _make_tracker(
        self,
        backend: FakeBackend,
        on_summary: Callable[[TurnSummaryResult], None] = _noop_callback,
    ) -> TurnSummaryTracker:
        return TurnSummaryTracker(
            backend=backend, model=_TEST_MODEL, on_summary=on_summary
        )

    @pytest.mark.asyncio
    async def test_track_accumulates_events(self):
        backend = FakeBackend(mock_llm_chunk(content="summary"))
        tracker = self._make_tracker(backend)
        tracker.start_turn("hello")
        tracker.track(AssistantEvent(content="chunk1"))
        tracker.track(AssistantEvent(content="chunk2"))
        assert tracker._data is not None
        assert tracker._data.assistant_fragments == ["chunk1", "chunk2"]

    @pytest.mark.asyncio
    async def test_end_turn_fires_summary(self):
        backend = FakeBackend(mock_llm_chunk(content="the summary"))
        tracker = self._make_tracker(backend)

        tracker.start_turn("do something")
        tracker.track(AssistantEvent(content="response"))
        tracker.end_turn()
        await asyncio.sleep(0.1)

        assert len(backend.requests_messages) == 1
        summary_msgs = backend.requests_messages[0]
        assert len(summary_msgs) == 2
        assert summary_msgs[0].role.value == "system"
        assert summary_msgs[1].role.value == "user"
        assert summary_msgs[1].content is not None
        assert "do something" in summary_msgs[1].content

    @pytest.mark.asyncio
    async def test_end_turn_sends_secondary_call_metadata(self):
        backend = FakeBackend(mock_llm_chunk(content="summary"))
        tracker = TurnSummaryTracker(
            backend=backend,
            model=_TEST_MODEL,
            session_metadata_getter=lambda: {
                "agent_entrypoint": "cli",
                "agent_version": "1.0.0",
                "client_name": "vibe_cli",
                "client_version": "1.0.0",
                "session_id": "session-123",
            },
        )

        tracker.start_turn("hello")
        tracker.end_turn()
        await asyncio.sleep(0.1)

        metadata = backend.requests_metadata[0]
        assert metadata is not None
        assert metadata["agent_entrypoint"] == "cli"
        assert metadata["agent_version"] == "1.0.0"
        assert metadata["client_name"] == "vibe_cli"
        assert metadata["client_version"] == "1.0.0"
        assert metadata["session_id"] == "session-123"
        assert "parent_session_id" not in metadata
        assert metadata["call_source"] == "vibe_code"
        assert metadata["call_type"] == "secondary_call"

    @pytest.mark.asyncio
    async def test_end_turn_sends_message_id_when_user_message_event_tracked(self):
        backend = FakeBackend(mock_llm_chunk(content="summary"))
        tracker = TurnSummaryTracker(
            backend=backend,
            model=_TEST_MODEL,
            session_metadata_getter=lambda: {"session_id": "session-123"},
        )

        tracker.start_turn("hello")
        tracker.track(UserMessageEvent(content="hello", message_id="message-456"))
        tracker.end_turn()
        await asyncio.sleep(0.1)

        metadata = backend.requests_metadata[0]
        assert metadata is not None
        assert metadata["message_id"] == "message-456"
        assert metadata["call_type"] == "secondary_call"

    @pytest.mark.asyncio
    async def test_end_turn_sends_parent_session_id_when_present(self):
        backend = FakeBackend(mock_llm_chunk(content="summary"))
        tracker = TurnSummaryTracker(
            backend=backend,
            model=_TEST_MODEL,
            session_metadata_getter=lambda: {
                "session_id": "session-123",
                "parent_session_id": "parent-session-456",
            },
        )

        tracker.start_turn("hello")
        tracker.end_turn()
        await asyncio.sleep(0.1)

        metadata = backend.requests_metadata[0]
        assert metadata is not None
        assert metadata["session_id"] == "session-123"
        assert metadata["parent_session_id"] == "parent-session-456"
        assert metadata["call_type"] == "secondary_call"

    @pytest.mark.asyncio
    async def test_end_turn_clears_state(self):
        backend = FakeBackend(mock_llm_chunk(content="summary"))
        tracker = self._make_tracker(backend)

        tracker.start_turn("hello")
        tracker.end_turn()
        assert tracker._data is None

    @pytest.mark.asyncio
    async def test_track_without_start_is_noop(self):
        backend = FakeBackend(mock_llm_chunk(content="summary"))
        tracker = self._make_tracker(backend)
        tracker.track(AssistantEvent(content="ignored"))
        assert tracker._data is None

    @pytest.mark.asyncio
    async def test_end_turn_without_start_is_noop(self):
        backend = FakeBackend(mock_llm_chunk(content="summary"))
        tracker = self._make_tracker(backend)
        tracker.end_turn()
        assert len(backend.requests_messages) == 0

    @pytest.mark.asyncio
    async def test_end_turn_after_cancel_is_noop(self):
        backend = FakeBackend(mock_llm_chunk(content="summary"))
        tracker = self._make_tracker(backend)
        tracker.start_turn("hello")
        tracker.cancel_turn()
        tracker.end_turn()
        await asyncio.sleep(0.1)
        assert len(backend.requests_messages) == 0

    @pytest.mark.asyncio
    async def test_on_summary_callback_called(self):
        backend = FakeBackend(mock_llm_chunk(content="the summary text"))
        received: list[TurnSummaryResult] = []

        def capture(result: TurnSummaryResult) -> None:
            received.append(result)

        tracker = self._make_tracker(backend, on_summary=capture)
        tracker.start_turn("hello")
        tracker.track(AssistantEvent(content="response"))
        tracker.end_turn()
        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].summary == "the summary text"
        assert received[0].generation == tracker.generation

    @pytest.mark.asyncio
    async def test_backend_error_calls_callback_with_none(self):
        backend = FakeBackend(exception_to_raise=RuntimeError("backend down"))
        received: list[TurnSummaryResult] = []

        def capture(result: TurnSummaryResult) -> None:
            received.append(result)

        tracker = self._make_tracker(backend, on_summary=capture)
        tracker.start_turn("hello")
        tracker.end_turn()
        await asyncio.sleep(0.2)

        assert len(received) == 1
        assert received[0].summary is None

    @pytest.mark.asyncio
    async def test_backend_error_logged_no_crash(self, caplog):
        backend = FakeBackend(exception_to_raise=RuntimeError("backend down"))
        tracker = self._make_tracker(backend)

        with caplog.at_level(logging.WARNING, logger="vibe"):
            tracker.start_turn("hello")
            tracker.end_turn()
            await asyncio.sleep(0.2)

        assert "Turn summary generation failed" in caplog.text

    @pytest.mark.asyncio
    async def test_close_cancels_pending_tasks(self):
        backend = FakeBackend(mock_llm_chunk(content="summary"))
        tracker = self._make_tracker(backend)

        tracker.start_turn("hello")
        tracker.end_turn()
        assert len(tracker._tasks) == 1

        await tracker.close()
        assert len(tracker._tasks) == 0

    @pytest.mark.asyncio
    async def test_error_only_turn_includes_error_in_summary(self):
        backend = FakeBackend(mock_llm_chunk(content="error summary"))
        received: list[TurnSummaryResult] = []

        def capture(result: TurnSummaryResult) -> None:
            received.append(result)

        tracker = self._make_tracker(backend, on_summary=capture)
        tracker.start_turn("do something")
        tracker.set_error("Rate limit exceeded")
        cancel = tracker.end_turn()
        await asyncio.sleep(0.1)

        assert cancel is not None
        assert len(backend.requests_messages) == 1
        prompt_content = backend.requests_messages[0][1].content
        assert prompt_content is not None
        assert "do something" in prompt_content
        assert "## Error" in prompt_content
        assert "Rate limit exceeded" in prompt_content
        assert "## Assistant Response" not in prompt_content
        assert len(received) == 1
        assert received[0].summary == "error summary"

    @pytest.mark.asyncio
    async def test_error_with_partial_response_includes_both(self):
        backend = FakeBackend(mock_llm_chunk(content="partial error summary"))
        tracker = self._make_tracker(backend)
        tracker.start_turn("do something")
        tracker.track(AssistantEvent(content="partial response"))
        tracker.set_error("connection lost")
        tracker.end_turn()
        await asyncio.sleep(0.1)

        assert len(backend.requests_messages) == 1
        prompt_content = backend.requests_messages[0][1].content
        assert prompt_content is not None
        assert "## Assistant Response" in prompt_content
        assert "partial response" in prompt_content
        assert "## Error" in prompt_content
        assert "connection lost" in prompt_content

    @pytest.mark.asyncio
    async def test_stale_summary_has_old_generation(self):
        backend = FakeBackend(mock_llm_chunk(content="stale summary"))
        received: list[TurnSummaryResult] = []

        def capture(result: TurnSummaryResult) -> None:
            received.append(result)

        tracker = self._make_tracker(backend, on_summary=capture)

        tracker.start_turn("turn 1")
        tracker.end_turn()

        tracker.start_turn("turn 2")

        await asyncio.sleep(0.1)

        assert len(received) == 1
        assert received[0].generation == 1
        assert tracker.generation == 2
        assert received[0].generation != tracker.generation


class TestNoopTurnSummary:
    def test_all_methods_callable(self):
        noop = NoopTurnSummary()
        noop.start_turn("hello")
        noop.track(AssistantEvent(content="chunk"))
        noop.set_error("some error")
        noop.cancel_turn()
        noop.end_turn()

    def test_generation_is_zero(self):
        noop = NoopTurnSummary()
        assert noop.generation == 0

    @pytest.mark.asyncio
    async def test_close_is_noop(self):
        noop = NoopTurnSummary()
        await noop.close()
