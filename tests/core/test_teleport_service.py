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
from vibe.core.teleport.nuage import GitHubStatus, TeleportSession
from vibe.core.teleport.teleport import TeleportService
from vibe.core.teleport.types import (
    TeleportAuthCompleteEvent,
    TeleportAuthRequiredEvent,
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportFetchingUrlEvent,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportStartingWorkflowEvent,
    TeleportWaitingForGitHubEvent,
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
            vibe_code_base_url="https://api.example.com",
            vibe_code_workflow_id="workflow-id",
            vibe_code_api_key="api-key",
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


class TestTeleportServiceBuildGitHubParams:
    @pytest.fixture
    def service(self, tmp_path: Path) -> TeleportService:
        mock_session_logger = MagicMock()
        return TeleportService(
            session_logger=mock_session_logger,
            vibe_code_base_url="https://api.example.com",
            vibe_code_workflow_id="workflow-id",
            vibe_code_api_key="api-key",
            workdir=tmp_path,
        )

    def test_builds_params_from_git_info(self, service: TeleportService) -> None:
        git_info = GitRepoInfo(
            remote_url="https://github.com/owner/repo.git",
            owner="owner",
            repo="repo",
            branch="main",
            commit="abc123",
            diff="",
        )
        params = service._build_github_params(git_info)

        assert params.repo == "owner/repo"
        assert params.branch == "main"
        assert params.commit == "abc123"
        assert params.teleported_diffs is None

    def test_includes_compressed_diff(self, service: TeleportService) -> None:
        git_info = GitRepoInfo(
            remote_url="https://github.com/owner/repo.git",
            owner="owner",
            repo="repo",
            branch="main",
            commit="abc123",
            diff="diff content",
        )
        params = service._build_github_params(git_info)

        assert params.teleported_diffs is not None


class TestTeleportServiceValidateConfig:
    def test_raises_when_no_api_key(self, tmp_path: Path) -> None:
        mock_session_logger = MagicMock()
        service = TeleportService(
            session_logger=mock_session_logger,
            vibe_code_base_url="https://api.example.com",
            vibe_code_workflow_id="workflow-id",
            vibe_code_api_key="",
            workdir=tmp_path,
        )
        with pytest.raises(ServiceTeleportError, match="MISTRAL_API_KEY not set"):
            service._validate_config()

    def test_passes_when_api_key_set(self, tmp_path: Path) -> None:
        mock_session_logger = MagicMock()
        service = TeleportService(
            session_logger=mock_session_logger,
            vibe_code_base_url="https://api.example.com",
            vibe_code_workflow_id="workflow-id",
            vibe_code_api_key="valid-key",
            workdir=tmp_path,
        )
        service._validate_config()

    def test_uses_custom_env_var_name_in_error(self, tmp_path: Path) -> None:
        mock_session_logger = MagicMock()
        mock_config = MagicMock()
        mock_config.vibe_code_api_key_env_var = "CUSTOM_API_KEY"
        service = TeleportService(
            session_logger=mock_session_logger,
            vibe_code_base_url="https://api.example.com",
            vibe_code_workflow_id="workflow-id",
            vibe_code_api_key="",
            workdir=tmp_path,
            vibe_config=mock_config,
        )
        with pytest.raises(ServiceTeleportError, match="CUSTOM_API_KEY not set"):
            service._validate_config()


class TestTeleportServiceCheckSupported:
    @pytest.fixture
    def service(self, tmp_path: Path) -> TeleportService:
        mock_session_logger = MagicMock()
        return TeleportService(
            session_logger=mock_session_logger,
            vibe_code_base_url="https://api.example.com",
            vibe_code_workflow_id="workflow-id",
            vibe_code_api_key="api-key",
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
            side_effect=ServiceTeleportNotSupportedError(
                "Teleport requires a git repository. cd into a project with a .git directory and try again."
            )
        )
        with pytest.raises(ServiceTeleportNotSupportedError):
            await service.check_supported()


class TestTeleportServiceIsSupported:
    @pytest.fixture
    def service(self, tmp_path: Path) -> TeleportService:
        mock_session_logger = MagicMock()
        return TeleportService(
            session_logger=mock_session_logger,
            vibe_code_base_url="https://api.example.com",
            vibe_code_workflow_id="workflow-id",
            vibe_code_api_key="api-key",
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
            vibe_code_base_url="https://api.example.com",
            vibe_code_workflow_id="workflow-id",
            vibe_code_api_key="api-key",
            workdir=tmp_path,
        )
        service._git.fetch = AsyncMock()
        service._git.is_branch_pushed = AsyncMock(return_value=True)
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

    @pytest.fixture
    def mock_github_connected(self) -> MagicMock:
        github_data = MagicMock()
        github_data.connected = True
        github_data.oauth_url = None
        github_data.status = GitHubStatus.CONNECTED
        return github_data

    @pytest.mark.asyncio
    async def test_execute_happy_path_github_already_connected(
        self,
        service: TeleportService,
        git_info: GitRepoInfo,
        mock_github_connected: MagicMock,
    ) -> None:
        service._git.get_info = AsyncMock(return_value=git_info)
        service._git.is_commit_pushed = AsyncMock(return_value=True)

        mock_nuage = MagicMock()
        mock_nuage.start_workflow = AsyncMock(return_value="exec-123")
        mock_nuage.get_github_integration = AsyncMock(
            return_value=mock_github_connected
        )
        mock_nuage.get_chat_assistant_url = AsyncMock(
            return_value="https://chat.example.com/123"
        )
        service._nuage_client_instance = mock_nuage

        session = TeleportSession()
        events = []
        gen = service.execute("test prompt", session)
        async for event in gen:
            events.append(event)

        assert isinstance(events[0], TeleportCheckingGitEvent)
        assert isinstance(events[1], TeleportStartingWorkflowEvent)
        assert isinstance(events[2], TeleportWaitingForGitHubEvent)
        assert isinstance(events[3], TeleportFetchingUrlEvent)
        assert isinstance(events[4], TeleportCompleteEvent)
        assert events[4].url == "https://chat.example.com/123"
        workflow_params = mock_nuage.start_workflow.call_args.args[0]
        assert workflow_params.integrations.chat_assistant is not None
        assert workflow_params.integrations.chat_assistant.project_name is None

    @pytest.mark.asyncio
    async def test_execute_requires_push_and_user_approves(
        self,
        service: TeleportService,
        git_info: GitRepoInfo,
        mock_github_connected: MagicMock,
    ) -> None:
        service._git.get_info = AsyncMock(return_value=git_info)
        service._git.is_commit_pushed = AsyncMock(return_value=False)
        service._git.get_unpushed_commit_count = AsyncMock(return_value=3)
        service._git.push_current_branch = AsyncMock(return_value=True)

        mock_nuage = MagicMock()
        mock_nuage.start_workflow = AsyncMock(return_value="exec-123")
        mock_nuage.get_github_integration = AsyncMock(
            return_value=mock_github_connected
        )
        mock_nuage.get_chat_assistant_url = AsyncMock(
            return_value="https://chat.example.com/123"
        )
        service._nuage_client_instance = mock_nuage

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
    async def test_execute_requires_oauth_flow(
        self, service: TeleportService, git_info: GitRepoInfo
    ) -> None:
        service._git.get_info = AsyncMock(return_value=git_info)
        service._git.is_commit_pushed = AsyncMock(return_value=True)

        github_pending = MagicMock()
        github_pending.connected = False
        github_pending.oauth_url = "https://github.com/login/oauth"
        github_pending.status = GitHubStatus.WAITING_FOR_OAUTH

        github_connected = MagicMock()
        github_connected.connected = True
        github_connected.oauth_url = None
        github_connected.status = GitHubStatus.CONNECTED

        mock_nuage = MagicMock()
        mock_nuage.start_workflow = AsyncMock(return_value="exec-123")
        mock_nuage.get_github_integration = AsyncMock(return_value=github_pending)
        mock_nuage.wait_for_github_connection = AsyncMock(return_value=github_connected)
        mock_nuage.get_chat_assistant_url = AsyncMock(
            return_value="https://chat.example.com/123"
        )
        service._nuage_client_instance = mock_nuage

        session = TeleportSession()
        events = []
        gen = service.execute("test prompt", session)
        async for event in gen:
            events.append(event)

        assert isinstance(events[0], TeleportCheckingGitEvent)
        assert isinstance(events[1], TeleportStartingWorkflowEvent)
        assert isinstance(events[2], TeleportWaitingForGitHubEvent)
        assert isinstance(events[3], TeleportAuthRequiredEvent)
        assert events[3].oauth_url == "https://github.com/login/oauth"
        assert isinstance(events[4], TeleportAuthCompleteEvent)
        assert isinstance(events[-1], TeleportCompleteEvent)

    @pytest.mark.asyncio
    async def test_execute_raises_when_chat_url_is_none(
        self,
        service: TeleportService,
        git_info: GitRepoInfo,
        mock_github_connected: MagicMock,
    ) -> None:
        service._git.get_info = AsyncMock(return_value=git_info)
        service._git.is_commit_pushed = AsyncMock(return_value=True)

        mock_nuage = MagicMock()
        mock_nuage.start_workflow = AsyncMock(return_value="exec-123")
        mock_nuage.get_github_integration = AsyncMock(
            return_value=mock_github_connected
        )
        mock_nuage.get_chat_assistant_url = AsyncMock(return_value=None)
        service._nuage_client_instance = mock_nuage

        session = TeleportSession()
        gen = service.execute("test prompt", session)

        with pytest.raises(ServiceTeleportError, match="not available"):
            async for _ in gen:
                pass

    @pytest.mark.asyncio
    async def test_execute_uses_default_prompt_when_none(
        self,
        service: TeleportService,
        git_info: GitRepoInfo,
        mock_github_connected: MagicMock,
    ) -> None:
        service._git.get_info = AsyncMock(return_value=git_info)
        service._git.is_commit_pushed = AsyncMock(return_value=True)

        mock_nuage = MagicMock()
        mock_nuage.start_workflow = AsyncMock(return_value="exec-123")
        mock_nuage.get_github_integration = AsyncMock(
            return_value=mock_github_connected
        )
        mock_nuage.get_chat_assistant_url = AsyncMock(
            return_value="https://chat.example.com/123"
        )
        service._nuage_client_instance = mock_nuage

        session = TeleportSession(
            messages=[{"role": "user", "content": "help me refactor"}]
        )
        gen = service.execute(None, session)
        async for _ in gen:
            pass

        call_args = mock_nuage.start_workflow.call_args
        assert "teleported" in call_args[0][0].prompt.lower()


class TestTeleportServiceContextManager:
    @pytest.mark.asyncio
    async def test_creates_client_on_enter(self, tmp_path: Path) -> None:
        mock_session_logger = MagicMock()
        service = TeleportService(
            session_logger=mock_session_logger,
            vibe_code_base_url="https://api.example.com",
            vibe_code_workflow_id="workflow-id",
            vibe_code_api_key="api-key",
            workdir=tmp_path,
        )
        assert service._client is None
        async with service:
            assert service._client is not None
            assert service._nuage_client_instance is not None
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
