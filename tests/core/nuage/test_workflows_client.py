from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.core.nuage.client import WorkflowsClient
from vibe.core.nuage.exceptions import ErrorCode, WorkflowsException
from vibe.core.nuage.streaming import StreamEvent, StreamEventsQueryParams
from vibe.core.nuage.workflow import WorkflowExecutionStatus


def _make_client() -> WorkflowsClient:
    return WorkflowsClient(base_url="http://localhost:8080", api_key="test-key")


def _valid_event_payload() -> dict:
    return {
        "stream": "test-stream",
        "timestamp_unix_nano": 1000000,
        "data": {"key": "value"},
    }


class TestParseSSEData:
    def test_valid_json_returns_stream_event(self) -> None:
        client = _make_client()
        payload = _valid_event_payload()
        result = client._parse_sse_data(json.dumps(payload), event_type=None)
        assert isinstance(result, StreamEvent)
        assert result.stream == "test-stream"
        assert result.data == {"key": "value"}

    def test_error_event_type_raises(self) -> None:
        client = _make_client()
        payload = {"some": "data"}
        with pytest.raises(WorkflowsException) as exc_info:
            client._parse_sse_data(json.dumps(payload), event_type="error")
        assert exc_info.value.code == ErrorCode.GET_EVENTS_STREAM_ERROR
        assert "Stream error from server" in exc_info.value.message

    def test_error_key_in_json_raises(self) -> None:
        client = _make_client()
        payload = {"error": "something went wrong"}
        with pytest.raises(WorkflowsException) as exc_info:
            client._parse_sse_data(json.dumps(payload), event_type=None)
        assert exc_info.value.code == ErrorCode.GET_EVENTS_STREAM_ERROR
        assert "something went wrong" in exc_info.value.message

    def test_error_event_type_with_non_dict_parsed(self) -> None:
        client = _make_client()
        with pytest.raises(WorkflowsException) as exc_info:
            client._parse_sse_data(json.dumps("a plain string"), event_type="error")
        assert "a plain string" in exc_info.value.message

    def test_malformed_json_raises(self) -> None:
        client = _make_client()
        with pytest.raises(json.JSONDecodeError):
            client._parse_sse_data("{not valid json", event_type=None)


class TestIterSSEEvents:
    @pytest.mark.asyncio
    async def test_parses_data_lines(self) -> None:
        client = _make_client()
        payload = _valid_event_payload()
        lines = [f"data: {json.dumps(payload)}"]
        response = AsyncMock()
        response.aiter_lines = _async_line_iter(lines)

        events = [e async for e in client._iter_sse_events(response)]
        assert len(events) == 1
        assert events[0].stream == "test-stream"

    @pytest.mark.asyncio
    async def test_skips_empty_lines_and_comments(self) -> None:
        client = _make_client()
        payload = _valid_event_payload()
        lines = ["", ": this is a comment", f"data: {json.dumps(payload)}", ""]
        response = AsyncMock()
        response.aiter_lines = _async_line_iter(lines)

        events = [e async for e in client._iter_sse_events(response)]
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_parses_event_type_and_passes_to_parse(self) -> None:
        client = _make_client()
        payload = {"error": "server broke"}
        lines = ["event: error", f"data: {json.dumps(payload)}"]
        response = AsyncMock()
        response.aiter_lines = _async_line_iter(lines)

        with pytest.raises(WorkflowsException) as exc_info:
            _ = [e async for e in client._iter_sse_events(response)]
        assert exc_info.value.code == ErrorCode.GET_EVENTS_STREAM_ERROR

    @pytest.mark.asyncio
    async def test_resets_event_type_after_data_line(self) -> None:
        client = _make_client()
        payload = _valid_event_payload()
        lines = [
            "event: custom_type",
            f"data: {json.dumps(payload)}",
            f"data: {json.dumps(payload)}",
        ]
        response = AsyncMock()
        response.aiter_lines = _async_line_iter(lines)

        with patch.object(
            client, "_parse_sse_data", wraps=client._parse_sse_data
        ) as mock_parse:
            events = [e async for e in client._iter_sse_events(response)]
            assert len(events) == 2
            assert mock_parse.call_args_list[0].args[1] == "custom_type"
            assert mock_parse.call_args_list[1].args[1] is None

    @pytest.mark.asyncio
    async def test_skips_non_data_non_event_lines(self) -> None:
        client = _make_client()
        payload = _valid_event_payload()
        lines = ["id: 123", "retry: 5000", f"data: {json.dumps(payload)}"]
        response = AsyncMock()
        response.aiter_lines = _async_line_iter(lines)

        events = [e async for e in client._iter_sse_events(response)]
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_parse_failure_logs_warning_and_continues(self) -> None:
        client = _make_client()
        payload = _valid_event_payload()
        lines = ["data: {not valid json}", f"data: {json.dumps(payload)}"]
        response = AsyncMock()
        response.aiter_lines = _async_line_iter(lines)

        with patch("vibe.core.nuage.client.logger") as mock_logger:
            events = [e async for e in client._iter_sse_events(response)]
            assert len(events) == 1
            mock_logger.warning.assert_called_once()


def _setup_mock_client(client: WorkflowsClient, mock_response: AsyncMock) -> None:
    mock_stream = AsyncMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_response)
    mock_stream.__aexit__ = AsyncMock(return_value=False)
    mock_http = AsyncMock()
    mock_http.stream = lambda *args, **kwargs: mock_stream
    client._client = mock_http


class TestStreamEvents:
    @pytest.mark.asyncio
    async def test_yields_stream_events(self) -> None:
        client = _make_client()
        payload = _valid_event_payload()
        lines = [f"data: {json.dumps(payload)}"]

        mock_response = AsyncMock()
        mock_response.raise_for_status = lambda: None
        mock_response.aiter_lines = _async_line_iter(lines)
        _setup_mock_client(client, mock_response)

        params = StreamEventsQueryParams(workflow_exec_id="wf-1", start_seq=0)
        events = [e async for e in client.stream_events(params)]
        assert len(events) == 1
        assert isinstance(events[0], StreamEvent)

    @pytest.mark.asyncio
    async def test_reraises_workflows_exception(self) -> None:
        client = _make_client()

        mock_response = AsyncMock()
        mock_response.raise_for_status = lambda: None
        mock_response.aiter_lines = _async_line_iter([
            "event: error",
            'data: {"error": "stream error"}',
        ])
        _setup_mock_client(client, mock_response)

        params = StreamEventsQueryParams(workflow_exec_id="wf-1")
        with pytest.raises(WorkflowsException) as exc_info:
            _ = [e async for e in client.stream_events(params)]
        assert exc_info.value.code == ErrorCode.GET_EVENTS_STREAM_ERROR

    @pytest.mark.asyncio
    async def test_wraps_other_exceptions_in_workflows_exception(self) -> None:
        client = _make_client()

        mock_response = AsyncMock()
        mock_response.raise_for_status.side_effect = RuntimeError("connection lost")
        _setup_mock_client(client, mock_response)

        params = StreamEventsQueryParams(workflow_exec_id="wf-1")
        with pytest.raises(WorkflowsException) as exc_info:
            _ = [e async for e in client.stream_events(params)]
        assert exc_info.value.code == ErrorCode.GET_EVENTS_STREAM_ERROR
        assert "Failed to stream events" in exc_info.value.message


class TestGetWorkflowRuns:
    @pytest.mark.asyncio
    async def test_sends_current_user_filter_by_default(self) -> None:
        client = _make_client()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"executions": [], "next_page_token": None}

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        client._client = mock_http

        await client.get_workflow_runs(
            workflow_identifier="workflow-1",
            page_size=10,
            status=[WorkflowExecutionStatus.RUNNING],
        )

        mock_http.get.assert_awaited_once()
        call_params = mock_http.get.call_args.kwargs["params"]
        assert call_params["user_id"] == "current"
        assert call_params["workflow_identifier"] == "workflow-1"
        assert call_params["page_size"] == 10

    @pytest.mark.asyncio
    async def test_allows_overriding_user_id(self) -> None:
        client = _make_client()
        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"executions": [], "next_page_token": None}

        mock_http = AsyncMock()
        mock_http.get.return_value = mock_response
        client._client = mock_http

        await client.get_workflow_runs(user_id="user-123")

        call_params = mock_http.get.call_args.kwargs["params"]
        assert call_params["user_id"] == "user-123"


def _async_line_iter(lines: list[str]):
    async def _iter():
        for line in lines:
            yield line

    return _iter
