from __future__ import annotations

import base64
import importlib
import os
from pathlib import Path
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import zstandard

from vibe.core.teleport.errors import (
    ServiceTeleportError,
    ServiceTeleportNotSupportedError,
)
from vibe.core.teleport.git import GitRepoInfo
from vibe.core.teleport.nuage import TeleportSession
from vibe.core.teleport.teleport import _NUAGE_EXECUTION_URL_TEMPLATE, TeleportService
from vibe.core.teleport.types import (
    TeleportAuthCompleteEvent,
    TeleportAuthRequiredEvent,
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportSendingGithubTokenEvent,
    TeleportStartingWorkflowEvent,
)


def _reimport_agent_loop() -> Any:
    to_clear = ("vibe.core.agent_loop", "git", "vibe.core.teleport")
    for k in [k for k in sys.modules if any(k.startswith(m) for m in to_clear)]:
        del sys.modules[k]
    return importlib.import_module("vibe.core.agent_loop")


class TestTeleportServiceCompressDiff:
    @pytest.fixture
    def service(self, tmp_path: Path) -> TeleportService:
        mock_session_logger = MagicMock()
        return TeleportService(
            session_logger=mock_session_logger,
            nuage_base_url="https://api.example.com",
            nuage_workflow_id="workflow-id",
            nuage_api_key="api-key",
            workdir=tmp_path,
        )

    def test_returns_none_for_empty_diff(self, service: TeleportService) -> None:
        assert service._compress_diff("") is None

    def test_compresses_and_encodes_diff(self, service: TeleportService) -> None:
        diff = "diff --git a/file.txt b/file.txt\n+new line"
        result = service._compress_diff(diff)

        assert result is not None
        decoded = base64.b64decode(result)
        decompressed = zstandard.ZstdDecompressor().decompress(decoded)
        assert decompressed.decode("utf-8") == diff

    def test_raises_when_diff_too_large(self, service: TeleportService) -> None:
        large_diff = "x" * 2_000_000
        with pytest.raises(ServiceTeleportError, match="Diff too large"):
            service._compress_diff(large_diff, max_size=100)


class TestTeleportServiceBuildSandbox:
    @pytest.fixture
    def service(self, tmp_path: Path) -> TeleportService:
        mock_session_logger = MagicMock()
        return TeleportService(
            session_logger=mock_session_logger,
            nuage_base_url="https://api.example.com",
            nuage_workflow_id="workflow-id",
            nuage_api_key="api-key",
            workdir=tmp_path,
        )

    def test_builds_sandbox_from_git_info(self, service: TeleportService) -> None:
        git_info = GitRepoInfo(
            remote_url="https://github.com/owner/repo.git",
            owner="owner",
            repo="repo",
            branch="main",
            commit="abc123",
            diff="",
        )
        sandbox = service._build_sandbox(git_info)

        assert sandbox.type == "new"
        assert sandbox.config.git_repo is not None
        assert sandbox.config.git_repo.url == "https://github.com/owner/repo.git"
        assert sandbox.config.git_repo.branch == "main"
        assert sandbox.config.git_repo.commit == "abc123"
        assert sandbox.teleported_diffs is None

    def test_includes_compressed_diff(self, service: TeleportService) -> None:
        git_info = GitRepoInfo(
            remote_url="https://github.com/owner/repo.git",
            owner="owner",
            repo="repo",
            branch="main",
            commit="abc123",
            diff="diff content",
        )
        sandbox = service._build_sandbox(git_info)

        assert sandbox.teleported_diffs is not None


class TestTeleportServiceValidateConfig:
    def test_raises_when_no_api_key(self, tmp_path: Path) -> None:
        mock_session_logger = MagicMock()
        service = TeleportService(
            session_logger=mock_session_logger,
            nuage_base_url="https://api.example.com",
            nuage_workflow_id="workflow-id",
            nuage_api_key="",
            workdir=tmp_path,
        )
        with pytest.raises(
            ServiceTeleportError, match="STAGING_MISTRAL_API_KEY not set"
        ):
            service._validate_config()

    def test_passes_when_api_key_set(self, tmp_path: Path) -> None:
        mock_session_logger = MagicMock()
        service = TeleportService(
            session_logger=mock_session_logger,
            nuage_base_url="https://api.example.com",
            nuage_workflow_id="workflow-id",
            nuage_api_key="valid-key",
            workdir=tmp_path,
        )
        service._validate_config()


class TestTeleportServiceCheckSupported:
    @pytest.fixture
    def service(self, tmp_path: Path) -> TeleportService:
        mock_session_logger = MagicMock()
        return TeleportService(
            session_logger=mock_session_logger,
            nuage_base_url="https://api.example.com",
            nuage_workflow_id="workflow-id",
            nuage_api_key="api-key",
            workdir=tmp_path,
        )

    @pytest.mark.asyncio
    async def test_check_supported_calls_git_info(
        self, service: TeleportService
    ) -> None:
        service._git.get_info = AsyncMock(
            return_value=GitRepoInfo(
                remote_url="https://github.com/owner/repo.git",
                owner="owner",
                repo="repo",
                branch="main",
                commit="abc123",
                diff="",
            )
        )
        await service.check_supported()
        service._git.get_info.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_supported_raises_when_not_supported(
        self, service: TeleportService
    ) -> None:
        service._git.get_info = AsyncMock(
            side_effect=ServiceTeleportNotSupportedError("Not a git repository")
        )
        with pytest.raises(ServiceTeleportNotSupportedError):
            await service.check_supported()


class TestTeleportServiceIsSupported:
    @pytest.fixture
    def service(self, tmp_path: Path) -> TeleportService:
        mock_session_logger = MagicMock()
        return TeleportService(
            session_logger=mock_session_logger,
            nuage_base_url="https://api.example.com",
            nuage_workflow_id="workflow-id",
            nuage_api_key="api-key",
            workdir=tmp_path,
        )

    @pytest.mark.asyncio
    async def test_is_supported_returns_true(self, service: TeleportService) -> None:
        service._git.is_supported = AsyncMock(return_value=True)
        assert await service.is_supported() is True

    @pytest.mark.asyncio
    async def test_is_supported_returns_false(self, service: TeleportService) -> None:
        service._git.is_supported = AsyncMock(return_value=False)
        assert await service.is_supported() is False


class TestTeleportServiceExecute:
    @pytest.fixture
    def service(self, tmp_path: Path) -> TeleportService:
        mock_session_logger = MagicMock()
        service = TeleportService(
            session_logger=mock_session_logger,
            nuage_base_url="https://api.example.com",
            nuage_workflow_id="workflow-id",
            nuage_api_key="api-key",
            workdir=tmp_path,
        )
        return service

    @pytest.fixture
    def git_info(self) -> GitRepoInfo:
        return GitRepoInfo(
            remote_url="https://github.com/owner/repo.git",
            owner="owner",
            repo="repo",
            branch="main",
            commit="abc123",
            diff="",
        )

    @pytest.mark.asyncio
    async def test_execute_happy_path_commit_pushed_with_token(
        self, service: TeleportService, git_info: GitRepoInfo
    ) -> None:
        service._git.get_info = AsyncMock(return_value=git_info)
        service._git.is_commit_pushed = AsyncMock(return_value=True)

        mock_github_auth = MagicMock()
        mock_github_auth.get_valid_token = AsyncMock(return_value="ghp_existing_token")
        service._github_auth = mock_github_auth

        mock_nuage = MagicMock()
        mock_nuage.start_workflow = AsyncMock(return_value="exec-123")
        mock_nuage.send_github_token = AsyncMock()
        service._nuage = mock_nuage

        session = TeleportSession()
        events = []
        gen = service.execute("test prompt", session)
        async for event in gen:
            events.append(event)

        assert isinstance(events[0], TeleportCheckingGitEvent)
        assert isinstance(events[1], TeleportStartingWorkflowEvent)
        assert isinstance(events[2], TeleportSendingGithubTokenEvent)
        assert isinstance(events[3], TeleportCompleteEvent)
        expected_url = _NUAGE_EXECUTION_URL_TEMPLATE.format(
            workflow_id="workflow-id", execution_id="exec-123"
        )
        assert events[3].url == expected_url

    @pytest.mark.asyncio
    async def test_execute_requires_push_and_user_approves(
        self, service: TeleportService, git_info: GitRepoInfo
    ) -> None:
        service._git.get_info = AsyncMock(return_value=git_info)
        service._git.is_commit_pushed = AsyncMock(return_value=False)
        service._git.get_unpushed_commit_count = AsyncMock(return_value=3)
        service._git.push_current_branch = AsyncMock(return_value=True)

        mock_github_auth = MagicMock()
        mock_github_auth.get_valid_token = AsyncMock(return_value="ghp_token")
        service._github_auth = mock_github_auth

        mock_nuage = MagicMock()
        mock_nuage.start_workflow = AsyncMock(return_value="exec-123")
        mock_nuage.send_github_token = AsyncMock()
        service._nuage = mock_nuage

        session = TeleportSession()
        events = []
        gen = service.execute("test prompt", session)

        event = await gen.asend(None)
        events.append(event)
        assert isinstance(event, TeleportCheckingGitEvent)

        event = await gen.asend(None)
        events.append(event)
        assert isinstance(event, TeleportPushRequiredEvent)
        assert event.unpushed_count == 3

        event = await gen.asend(TeleportPushResponseEvent(approved=True))
        events.append(event)
        assert isinstance(event, TeleportPushingEvent)

        async for event in gen:
            events.append(event)

        assert isinstance(events[-1], TeleportCompleteEvent)

    @pytest.mark.asyncio
    async def test_execute_requires_push_and_user_declines(
        self, service: TeleportService, git_info: GitRepoInfo
    ) -> None:
        service._git.get_info = AsyncMock(return_value=git_info)
        service._git.is_commit_pushed = AsyncMock(return_value=False)
        service._git.get_unpushed_commit_count = AsyncMock(return_value=1)

        session = TeleportSession()
        gen = service.execute("test prompt", session)

        await gen.asend(None)
        await gen.asend(None)

        with pytest.raises(ServiceTeleportError, match="Teleport cancelled"):
            await gen.asend(TeleportPushResponseEvent(approved=False))

    @pytest.mark.asyncio
    async def test_execute_requires_auth_flow(
        self, service: TeleportService, git_info: GitRepoInfo
    ) -> None:
        service._git.get_info = AsyncMock(return_value=git_info)
        service._git.is_commit_pushed = AsyncMock(return_value=True)

        mock_handle = MagicMock()
        mock_handle.info.user_code = "ABC-123"
        mock_handle.info.verification_uri = "https://github.com/login/device"

        mock_github_auth = MagicMock()
        mock_github_auth.get_valid_token = AsyncMock(return_value=None)
        mock_github_auth.start_device_flow = AsyncMock(return_value=mock_handle)
        mock_github_auth.wait_for_token = AsyncMock(return_value="ghp_new_token")
        service._github_auth = mock_github_auth

        mock_nuage = MagicMock()
        mock_nuage.start_workflow = AsyncMock(return_value="exec-123")
        mock_nuage.send_github_token = AsyncMock()
        service._nuage = mock_nuage

        session = TeleportSession()
        events = []
        gen = service.execute("test prompt", session)
        async for event in gen:
            events.append(event)

        assert isinstance(events[0], TeleportCheckingGitEvent)
        assert isinstance(events[1], TeleportAuthRequiredEvent)
        assert events[1].user_code == "ABC-123"
        assert isinstance(events[2], TeleportAuthCompleteEvent)
        assert isinstance(events[3], TeleportStartingWorkflowEvent)
        assert isinstance(events[-1], TeleportCompleteEvent)

    @pytest.mark.asyncio
    async def test_execute_uses_default_prompt_when_none(
        self, service: TeleportService, git_info: GitRepoInfo
    ) -> None:
        service._git.get_info = AsyncMock(return_value=git_info)
        service._git.is_commit_pushed = AsyncMock(return_value=True)

        mock_github_auth = MagicMock()
        mock_github_auth.get_valid_token = AsyncMock(return_value="ghp_token")
        service._github_auth = mock_github_auth

        mock_nuage = MagicMock()
        mock_nuage.start_workflow = AsyncMock(return_value="exec-123")
        mock_nuage.send_github_token = AsyncMock()
        service._nuage = mock_nuage

        session = TeleportSession()
        gen = service.execute(None, session)
        async for _ in gen:
            pass

        call_args = mock_nuage.start_workflow.call_args
        assert "continue where you left off" in call_args[0][0].prompt


class TestTeleportServiceContextManager:
    @pytest.mark.asyncio
    async def test_creates_client_on_enter(self, tmp_path: Path) -> None:
        mock_session_logger = MagicMock()
        service = TeleportService(
            session_logger=mock_session_logger,
            nuage_base_url="https://api.example.com",
            nuage_workflow_id="workflow-id",
            nuage_api_key="api-key",
            workdir=tmp_path,
        )
        assert service._client is None
        async with service:
            assert service._client is not None
            assert service._github_auth is not None
            assert service._nuage is not None
        assert service._client is None


class TestTeleportAvailability:
    def test_teleport_available_is_false_when_git_not_installed(self) -> None:
        with patch.dict(os.environ, {"GIT_PYTHON_GIT_EXECUTABLE": "/nonexistent/git"}):
            agent_loop = _reimport_agent_loop()
            assert agent_loop._TELEPORT_AVAILABLE is False

    def test_teleport_service_raises_error_when_git_not_available(self) -> None:
        with patch.dict(os.environ, {"GIT_PYTHON_GIT_EXECUTABLE": "/nonexistent/git"}):
            agent_loop = _reimport_agent_loop()
            with pytest.raises(agent_loop.TeleportError, match="git to be installed"):
                agent_loop.AgentLoop.teleport_service.fget(
                    MagicMock(_teleport_service=None)
                )

    def test_teleport_available_is_true_when_git_installed(
        self, tmp_path: Path
    ) -> None:
        fake_git = tmp_path / "git"
        fake_git.write_text("#!/bin/sh\necho 'git version 2.0.0'")
        fake_git.chmod(0o755)
        with patch.dict(os.environ, {"GIT_PYTHON_GIT_EXECUTABLE": str(fake_git)}):
            agent_loop = _reimport_agent_loop()
            assert agent_loop._TELEPORT_AVAILABLE is True
