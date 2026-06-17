from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from vibe.core.nuage.workflow import (
    WorkflowExecutionListResponse,
    WorkflowExecutionStatus,
    WorkflowExecutionWithoutResultResponse,
)
from vibe.core.session.resume_sessions import (
    RemoteResumeResult,
    RemoteResumeSessions,
    ResumeSessionInfo,
    can_delete_resume_session_source,
    list_remote_resume_sessions,
    session_latest_messages,
    short_session_id,
)
from vibe.core.session.session_id import shorten_session_id


@dataclass(frozen=True)
class RemoteResumeRequest:
    workflow_identifier: str | None
    page_size: int
    status: Sequence[WorkflowExecutionStatus] | None


class FakeRemoteResumeClient:
    def __init__(
        self,
        response: WorkflowExecutionListResponse | None = None,
        *,
        delay: float = 0.0,
    ) -> None:
        self.response = response or WorkflowExecutionListResponse(executions=[])
        self.delay = delay
        self.requests: list[RemoteResumeRequest] = []
        self.closed = False

    async def get_workflow_runs(
        self,
        workflow_identifier: str | None = None,
        page_size: int = 50,
        next_page_token: str | None = None,
        status: Sequence[WorkflowExecutionStatus] | None = None,
        user_id: str = "current",
    ) -> WorkflowExecutionListResponse:
        self.requests.append(
            RemoteResumeRequest(
                workflow_identifier=workflow_identifier,
                page_size=page_size,
                status=status,
            )
        )
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.response

    async def aclose(self) -> None:
        self.closed = True


def enabled_vibe_code_config() -> MagicMock:
    config = MagicMock()
    config.vibe_code_enabled = True
    config.vibe_code_api_key = "test-key"
    config.vibe_code_base_url = "https://test.example.com"
    config.api_timeout = 30
    config.vibe_code_workflow_id = "workflow-1"
    return config


class TestShortenSessionId:
    def test_shortens_to_first_8_chars(self) -> None:
        sid = "abcdef1234567890"
        assert shorten_session_id(sid) == "abcdef12"

    def test_from_end_shortens_to_last_8_chars(self) -> None:
        sid = "abcdef1234567890"
        assert shorten_session_id(sid, from_end=True) == "34567890"

    def test_returns_full_id_when_shorter_than_limit(self) -> None:
        sid = "abc"
        assert shorten_session_id(sid) == "abc"
        assert shorten_session_id(sid, from_end=True) == "abc"


class TestShortSessionId:
    def test_local_delegates_to_shorten(self) -> None:
        sid = "abcdef1234567890"
        assert short_session_id(sid) == shorten_session_id(sid)

    def test_local_is_default(self) -> None:
        sid = "abcdef1234567890"
        assert short_session_id(sid) == short_session_id(sid, source="local")

    def test_remote_delegates_to_shorten_from_end(self) -> None:
        sid = "abcdef1234567890"
        assert short_session_id(sid, source="remote") == shorten_session_id(
            sid, from_end=True
        )

    def test_empty_string(self) -> None:
        assert short_session_id("") == ""


class TestCanDeleteResumeSession:
    def test_local_source_can_delete(self) -> None:
        assert can_delete_resume_session_source("local") is True

    def test_remote_source_cannot_delete(self) -> None:
        assert can_delete_resume_session_source("remote") is False

    def test_session_info_can_delete_matches_source(self) -> None:
        session = ResumeSessionInfo(
            session_id="session-a",
            source="local",
            cwd="/test",
            title=None,
            end_time=None,
        )

        assert session.can_delete is True


class TestListRemoteResumeSessions:
    @pytest.mark.asyncio
    async def test_passes_active_statuses_to_api(self) -> None:
        running = WorkflowExecutionWithoutResultResponse(
            workflow_name="vibe",
            execution_id="exec-running",
            status=WorkflowExecutionStatus.RUNNING,
            start_time=datetime(2026, 1, 1),
            end_time=None,
        )
        continued = WorkflowExecutionWithoutResultResponse(
            workflow_name="vibe",
            execution_id="exec-continued",
            status=WorkflowExecutionStatus.CONTINUED_AS_NEW,
            start_time=datetime(2026, 1, 1),
            end_time=None,
        )

        mock_response = WorkflowExecutionListResponse(executions=[running, continued])
        client = FakeRemoteResumeClient(mock_response)

        result = await list_remote_resume_sessions(client, "workflow-1")

        assert len(result) == 2
        session_ids = {s.session_id for s in result}
        assert "exec-running" in session_ids
        assert "exec-continued" in session_ids
        assert all(s.source == "remote" for s in result)
        assert client.requests == [
            RemoteResumeRequest(
                workflow_identifier="workflow-1",
                page_size=50,
                status=[
                    WorkflowExecutionStatus.RUNNING,
                    WorkflowExecutionStatus.CONTINUED_AS_NEW,
                ],
            )
        ]

    @pytest.mark.asyncio
    async def test_deduplicates_execution_ids_keeps_latest(self) -> None:
        older = WorkflowExecutionWithoutResultResponse(
            workflow_name="vibe",
            execution_id="exec-1",
            status=WorkflowExecutionStatus.RUNNING,
            start_time=datetime(2026, 1, 1),
            end_time=None,
        )
        newer = WorkflowExecutionWithoutResultResponse(
            workflow_name="vibe",
            execution_id="exec-1",
            status=WorkflowExecutionStatus.RUNNING,
            start_time=datetime(2026, 1, 5),
            end_time=None,
        )
        other = WorkflowExecutionWithoutResultResponse(
            workflow_name="vibe",
            execution_id="exec-2",
            status=WorkflowExecutionStatus.RUNNING,
            start_time=datetime(2026, 1, 3),
            end_time=None,
        )

        mock_response = WorkflowExecutionListResponse(executions=[older, newer, other])
        client = FakeRemoteResumeClient(mock_response)

        result = await list_remote_resume_sessions(client, "workflow-1")

        assert len(result) == 2
        by_id = {s.session_id: s for s in result}
        assert by_id["exec-1"].end_time == datetime(2026, 1, 5).isoformat()
        assert "exec-2" in by_id

    @pytest.mark.asyncio
    async def test_dedup_keeps_latest_start_time_when_previous_has_end_time(
        self,
    ) -> None:
        previous = WorkflowExecutionWithoutResultResponse(
            workflow_name="vibe",
            execution_id="exec-1",
            status=WorkflowExecutionStatus.FAILED,
            start_time=datetime(2026, 1, 1),
            end_time=datetime(2026, 1, 10),
        )
        newer = WorkflowExecutionWithoutResultResponse(
            workflow_name="vibe",
            execution_id="exec-1",
            status=WorkflowExecutionStatus.RUNNING,
            start_time=datetime(2026, 1, 5),
            end_time=None,
        )

        mock_response = WorkflowExecutionListResponse(executions=[previous, newer])
        client = FakeRemoteResumeClient(mock_response)

        result = await list_remote_resume_sessions(client, "workflow-1")

        assert len(result) == 1
        assert result[0].session_id == "exec-1"
        assert result[0].status == WorkflowExecutionStatus.RUNNING


class TestSessionLatestMessages:
    def test_remote_session_formats_title_and_status(self) -> None:
        session = ResumeSessionInfo(
            session_id="exec-1",
            source="remote",
            cwd="",
            title="My run",
            end_time=None,
            status="RUNNING",
        )
        messages = session_latest_messages([session], MagicMock())
        assert messages[session.option_id] == "My run (running)"


class TestRemoteResumeSessions:
    @pytest.mark.asyncio
    async def test_fetch_skips_when_vibe_code_disabled(self) -> None:
        config = MagicMock()
        config.vibe_code_enabled = False
        config.vibe_code_api_key = "key"
        created_clients: list[FakeRemoteResumeClient] = []

        def client_factory(_config: object) -> FakeRemoteResumeClient:
            client = FakeRemoteResumeClient()
            created_clients.append(client)
            return client

        remote = RemoteResumeSessions(lambda: config, client_factory)

        result = await remote.fetch(10.0)

        assert result == RemoteResumeResult([], None)
        assert created_clients == []

    @pytest.mark.asyncio
    async def test_start_cancels_previous_fetch(self) -> None:
        config = enabled_vibe_code_config()
        client = FakeRemoteResumeClient(delay=10.0)
        remote = RemoteResumeSessions(lambda: config, lambda _config: client)

        first = remote.start(100.0)
        await asyncio.sleep(0)
        second = remote.start(100.0)
        await asyncio.sleep(0)
        await remote.aclose()

        assert first.cancelled()
        assert first is not second

    @pytest.mark.asyncio
    async def test_fetch_returns_error_tuple_on_timeout(self) -> None:
        config = enabled_vibe_code_config()
        client = FakeRemoteResumeClient(delay=10.0)
        remote = RemoteResumeSessions(lambda: config, lambda _config: client)

        sessions, error = await remote.fetch(0.01)
        await remote.aclose()

        assert sessions == []
        assert error is not None and "Timed out" in error

    @pytest.mark.asyncio
    async def test_aclose_cancels_inflight_fetch_before_closing_client(self) -> None:
        config = enabled_vibe_code_config()
        client = FakeRemoteResumeClient(delay=10.0)
        remote = RemoteResumeSessions(lambda: config, lambda _config: client)

        task = remote.start(100.0)
        await asyncio.sleep(0)
        await remote.aclose()

        assert task.cancelled()
        assert client.closed is True
