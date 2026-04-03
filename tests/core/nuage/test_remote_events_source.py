from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.agent_loop import AgentLoopStateError
from vibe.core.nuage.exceptions import ErrorCode, WorkflowsException
from vibe.core.nuage.remote_events_source import RemoteEventsSource
from vibe.core.nuage.streaming import StreamEvent

_SESSION_ID = "test-session"


def _make_source(**kwargs) -> RemoteEventsSource:
    config = build_test_vibe_config(enabled_tools=kwargs.pop("enabled_tools", []))
    return RemoteEventsSource(session_id=_SESSION_ID, config=config, **kwargs)


def _make_retryable_exc(msg: str) -> WorkflowsException:
    return WorkflowsException(message=msg, code=ErrorCode.GET_EVENTS_STREAM_ERROR)


def _make_stream_event(
    broker_sequence: int | None = None, data: dict | None = None
) -> StreamEvent:
    return StreamEvent(data=data or {}, broker_sequence=broker_sequence)


class TestIsRetryableStreamDisconnect:
    def test_peer_closed_connection(self) -> None:
        source = _make_source()
        exc = _make_retryable_exc("Peer closed connection without response")
        assert source._is_retryable_stream_disconnect(exc) is True

    def test_incomplete_chunked_read(self) -> None:
        source = _make_source()
        exc = _make_retryable_exc("Incomplete chunked read during streaming")
        assert source._is_retryable_stream_disconnect(exc) is True

    def test_non_retryable_message(self) -> None:
        source = _make_source()
        exc = WorkflowsException(
            message="some other error", code=ErrorCode.GET_EVENTS_STREAM_ERROR
        )
        assert source._is_retryable_stream_disconnect(exc) is False

    def test_wrong_error_code(self) -> None:
        source = _make_source()
        exc = WorkflowsException(
            message="peer closed connection",
            code=ErrorCode.POST_EXECUTIONS_SIGNALS_ERROR,
        )
        assert source._is_retryable_stream_disconnect(exc) is False


async def _async_gen_from_list(items):
    for item in items:
        yield item


async def _async_gen_raise(items, exc):
    for item in items:
        yield item
    raise exc


class _FakeStream:
    def __init__(self, payloads=None, exc=None):
        self._payloads = payloads or []
        self._exc = exc
        self._closed = False

    def __aiter__(self):
        return self._iterate().__aiter__()

    async def _iterate(self):
        for p in self._payloads:
            yield p
        if self._exc is not None:
            raise self._exc

    async def aclose(self):
        self._closed = True


class TestStreamRemoteEventsRetry:
    @pytest.mark.asyncio
    async def test_retries_on_retryable_disconnect(self) -> None:
        source = _make_source()
        exc = _make_retryable_exc("peer closed connection")

        call_count = 0

        def make_stream(_params):
            nonlocal call_count
            call_count += 1
            return _FakeStream(exc=exc)

        mock_client = MagicMock()
        mock_client.stream_events = make_stream
        source._client = mock_client

        with patch("asyncio.sleep", new_callable=AsyncMock):
            events = [e async for e in source._stream_remote_events()]

        assert events == []
        assert call_count == 4  # 1 initial + 3 retries

    @pytest.mark.asyncio
    async def test_stops_after_max_retry_count(self) -> None:
        source = _make_source()
        exc = _make_retryable_exc("incomplete chunked read")

        call_count = 0

        def make_stream(_params):
            nonlocal call_count
            call_count += 1
            return _FakeStream(exc=exc)

        mock_client = MagicMock()
        mock_client.stream_events = make_stream
        source._client = mock_client

        with patch("asyncio.sleep", new_callable=AsyncMock):
            events = [e async for e in source._stream_remote_events()]

        assert events == []
        assert call_count == 4

    @pytest.mark.asyncio
    async def test_resets_retry_count_on_successful_event(self) -> None:
        source = _make_source()
        exc = _make_retryable_exc("peer closed connection")

        successful_event = _make_stream_event(broker_sequence=0, data={})
        call_count = 0

        def make_stream(_params):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return _FakeStream(payloads=[successful_event], exc=exc)
            return _FakeStream(exc=exc)

        mock_client = MagicMock()
        mock_client.stream_events = make_stream
        source._client = mock_client

        with (
            patch("asyncio.sleep", new_callable=AsyncMock),
            patch.object(source, "_normalize_stream_event", return_value=None),
        ):
            events = [e async for e in source._stream_remote_events()]

        assert events == []
        # call 1: success + exc -> retry_count = 1
        # call 2: success (reset) + exc -> retry_count = 1
        # call 3: exc -> retry_count = 2
        # call 4: exc -> retry_count = 3
        # call 5: exc -> retry_count = 4 > 3 -> break
        assert call_count == 5

    @pytest.mark.asyncio
    async def test_non_retryable_raises_agent_loop_state_error(self) -> None:
        source = _make_source()
        exc = WorkflowsException(
            message="something bad", code=ErrorCode.TEMPORAL_CONNECTION_ERROR
        )

        mock_client = MagicMock()
        mock_client.stream_events = lambda _: _FakeStream(exc=exc)
        source._client = mock_client

        with pytest.raises(AgentLoopStateError):
            async for _ in source._stream_remote_events():
                pass


class TestStreamRemoteEventsIdleBoundary:
    @pytest.mark.asyncio
    async def test_stops_on_idle_boundary(self) -> None:
        source = _make_source()
        event_data = _make_stream_event(broker_sequence=0, data={})
        sentinel_event = MagicMock()

        mock_client = MagicMock()
        mock_client.stream_events = lambda _: _FakeStream(payloads=[event_data])
        source._client = mock_client

        workflow_event = MagicMock()

        with (
            patch.object(
                source, "_normalize_stream_event", return_value=workflow_event
            ),
            patch.object(
                source, "_consume_workflow_event", return_value=[sentinel_event]
            ),
            patch.object(source, "_is_idle_boundary", return_value=True) as mock_idle,
        ):
            events = [
                e
                async for e in source._stream_remote_events(stop_on_idle_boundary=True)
            ]

        assert events == [sentinel_event]
        mock_idle.assert_called_once_with(workflow_event)

    @pytest.mark.asyncio
    async def test_continues_past_idle_boundary_when_disabled(self) -> None:
        source = _make_source()
        event1 = _make_stream_event(broker_sequence=0, data={})
        event2 = _make_stream_event(broker_sequence=1, data={})
        sentinel1 = MagicMock()
        sentinel2 = MagicMock()

        mock_client = MagicMock()
        mock_client.stream_events = lambda _: _FakeStream(payloads=[event1, event2])
        source._client = mock_client

        workflow_event = MagicMock()
        call_count = 0

        def consume_side_effect(_evt):
            nonlocal call_count
            call_count += 1
            return [sentinel1] if call_count == 1 else [sentinel2]

        with (
            patch.object(
                source, "_normalize_stream_event", return_value=workflow_event
            ),
            patch.object(
                source, "_consume_workflow_event", side_effect=consume_side_effect
            ),
            patch.object(source, "_is_idle_boundary", return_value=True),
        ):
            events = [
                e
                async for e in source._stream_remote_events(stop_on_idle_boundary=False)
            ]

        assert events == [sentinel1, sentinel2]


class TestBrokerSequenceTracking:
    @pytest.mark.asyncio
    async def test_next_start_seq_updated(self) -> None:
        source = _make_source()
        assert source._next_start_seq == 0

        event1 = _make_stream_event(broker_sequence=5, data={})
        event2 = _make_stream_event(broker_sequence=10, data={})

        mock_client = MagicMock()
        mock_client.stream_events = lambda _: _FakeStream(payloads=[event1, event2])
        source._client = mock_client

        with patch.object(source, "_normalize_stream_event", return_value=None):
            events = [e async for e in source._stream_remote_events()]

        assert events == []
        assert source._next_start_seq == 11

    @pytest.mark.asyncio
    async def test_none_broker_sequence_not_updated(self) -> None:
        source = _make_source()
        source._next_start_seq = 5

        event = _make_stream_event(broker_sequence=None, data={})

        mock_client = MagicMock()
        mock_client.stream_events = lambda _: _FakeStream(payloads=[event])
        source._client = mock_client

        with patch.object(source, "_normalize_stream_event", return_value=None):
            events = [e async for e in source._stream_remote_events()]

        assert events == []
        assert source._next_start_seq == 5
