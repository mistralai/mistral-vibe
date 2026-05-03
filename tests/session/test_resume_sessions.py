from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.core.session.resume_sessions import (
    SHORT_SESSION_ID_LEN,
    list_remote_resume_sessions,
    short_session_id,
)


class TestShortSessionId:
    def test_local_shortens_to_first_chars(self) -> None:
        sid = "abcdef1234567890"
        result = short_session_id(sid)
        assert result == sid[:SHORT_SESSION_ID_LEN]
        assert len(result) == SHORT_SESSION_ID_LEN

    def test_local_is_default(self) -> None:
        sid = "abcdef1234567890"
        assert short_session_id(sid) == short_session_id(sid, source="local")

    def test_remote_shortens_to_last_chars(self) -> None:
        sid = "abcdef1234567890"
        result = short_session_id(sid, source="remote")
        assert result == sid[-SHORT_SESSION_ID_LEN:]
        assert len(result) == SHORT_SESSION_ID_LEN

    def test_returns_full_id_when_shorter_than_limit(self) -> None:
        sid = "abc"
        assert short_session_id(sid) == "abc"
        assert short_session_id(sid, source="remote") == "abc"

    def test_empty_string(self) -> None:
        assert short_session_id("") == ""


class TestListRemoteResumeSessions:
    @pytest.mark.asyncio
    async def test_returns_empty_when_vibe_code_disabled(self) -> None:
        config = MagicMock()
        config.vibe_code_enabled = False
        config.vibe_code_api_key = "key"
        result = await list_remote_resume_sessions(config)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_api_key(self) -> None:
        config = MagicMock()
        config.vibe_code_enabled = True
        config.vibe_code_api_key = None
        result = await list_remote_resume_sessions(config)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_both_missing(self) -> None:
        config = MagicMock()
        config.vibe_code_enabled = False
        config.vibe_code_api_key = None
        result = await list_remote_resume_sessions(config)
        assert result == []

    @pytest.mark.asyncio
    async def test_passes_active_statuses_to_api(self) -> None:
        from datetime import datetime

        from vibe.core.nuage.workflow import (
            WorkflowExecutionListResponse,
            WorkflowExecutionStatus,
            WorkflowExecutionWithoutResultResponse,
        )

        running = WorkflowExecutionWithoutResultResponse(
            workflow_name="vibe",
            execution_id="exec-running",
            status=WorkflowExecutionStatus.RUNNING,
            start_time=datetime(2026, 1, 1),
            end_time=None,
        )
        retrying = WorkflowExecutionWithoutResultResponse(
            workflow_name="vibe",
            execution_id="exec-retrying",
            status=WorkflowExecutionStatus.RETRYING_AFTER_ERROR,
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

        mock_response = WorkflowExecutionListResponse(
            executions=[running, retrying, continued]
        )

        config = MagicMock()
        config.vibe_code_enabled = True
        config.vibe_code_api_key = "test-key"
        config.vibe_code_base_url = "https://test.example.com"
        config.api_timeout = 30
        config.vibe_code_workflow_id = "workflow-1"

        with patch("vibe.core.session.resume_sessions.WorkflowsClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get_workflow_runs.return_value = mock_response
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await list_remote_resume_sessions(config)

        assert len(result) == 3
        session_ids = {s.session_id for s in result}
        assert "exec-running" in session_ids
        assert "exec-retrying" in session_ids
        assert "exec-continued" in session_ids
        assert all(s.source == "remote" for s in result)

        mock_client.get_workflow_runs.assert_called_once_with(
            workflow_identifier="workflow-1",
            page_size=50,
            status=[
                WorkflowExecutionStatus.RUNNING,
                WorkflowExecutionStatus.RETRYING_AFTER_ERROR,
                WorkflowExecutionStatus.CONTINUED_AS_NEW,
            ],
        )

    @pytest.mark.asyncio
    async def test_deduplicates_execution_ids_keeps_latest(self) -> None:
        from datetime import datetime

        from vibe.core.nuage.workflow import (
            WorkflowExecutionListResponse,
            WorkflowExecutionStatus,
            WorkflowExecutionWithoutResultResponse,
        )

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

        config = MagicMock()
        config.vibe_code_enabled = True
        config.vibe_code_api_key = "test-key"
        config.vibe_code_base_url = "https://test.example.com"
        config.api_timeout = 30
        config.vibe_code_workflow_id = "workflow-1"

        with patch("vibe.core.session.resume_sessions.WorkflowsClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get_workflow_runs.return_value = mock_response
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await list_remote_resume_sessions(config)

        assert len(result) == 2
        by_id = {s.session_id: s for s in result}
        assert by_id["exec-1"].end_time == datetime(2026, 1, 5).isoformat()
        assert "exec-2" in by_id

    @pytest.mark.asyncio
    async def test_dedup_keeps_latest_start_time_when_previous_has_end_time(
        self,
    ) -> None:
        from datetime import datetime

        from vibe.core.nuage.workflow import (
            WorkflowExecutionListResponse,
            WorkflowExecutionStatus,
            WorkflowExecutionWithoutResultResponse,
        )

        previous = WorkflowExecutionWithoutResultResponse(
            workflow_name="vibe",
            execution_id="exec-1",
            status=WorkflowExecutionStatus.RETRYING_AFTER_ERROR,
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

        config = MagicMock()
        config.vibe_code_enabled = True
        config.vibe_code_api_key = "test-key"
        config.vibe_code_base_url = "https://test.example.com"
        config.api_timeout = 30
        config.vibe_code_workflow_id = "workflow-1"

        with patch("vibe.core.session.resume_sessions.WorkflowsClient") as MockClient:
            mock_client = AsyncMock()
            mock_client.get_workflow_runs.return_value = mock_response
            MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await list_remote_resume_sessions(config)

        assert len(result) == 1
        assert result[0].session_id == "exec-1"
        assert result[0].status == WorkflowExecutionStatus.RUNNING
