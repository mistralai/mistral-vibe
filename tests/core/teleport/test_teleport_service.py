from __future__ import annotations

import base64
import json
from pathlib import Path
import threading
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import zstandard

from tests.constants import TELEPORT_COMPLETE_URL, TELEPORT_SESSIONS_PATH
from vibe.core.teleport.errors import (
    ServiceTeleportError,
    ServiceTeleportNotSupportedError,
)
from vibe.core.teleport.git import GitRepoInfo
from vibe.core.teleport.teleport import TeleportService
from vibe.core.teleport.types import (
    TeleportCheckingGitEvent,
    TeleportCompleteEvent,
    TeleportMessageContext,
    TeleportMessageContextSource,
    TeleportPushingEvent,
    TeleportPushRequiredEvent,
    TeleportPushResponseEvent,
    TeleportStartingWorkflowEvent,
)
from vibe.core.vibe_code_project import VibeProjectsStore
from vibe.utils.http import VibeAsyncHTTPClient


def _make_service(tmp_path: Path, **kwargs: Any) -> TeleportService:
    return TeleportService(
        vibe_code_sessions_base_url=kwargs.pop(
            "vibe_code_sessions_base_url", "https://api.example.com"
        ),
        api_key=kwargs.pop("api_key", "api-key"),
        workdir=tmp_path,
        **kwargs,
    )


def _mock_handler() -> Any:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "sessionId": "controller-session-id",
                "webSessionId": "web-session-id",
                "projectId": "project-id",
                "status": "running",
                "url": TELEPORT_COMPLETE_URL,
            },
        )

    return handler


class TrackingProjectsStore(VibeProjectsStore):
    def __init__(self, path: Path | str | None = None) -> None:
        super().__init__(path)
        self.delete_remote_project_threads: list[int] = []

    def delete_remote_project(self, *, repo_root: Path) -> None:
        self.delete_remote_project_threads.append(threading.get_ident())
        super().delete_remote_project(repo_root=repo_root)


class TestTeleportServiceCompressDiff:
    @pytest.fixture
    def service(self, tmp_path: Path) -> TeleportService:
        return _make_service(tmp_path)

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


class TestTeleportServiceValidateConfig:
    def test_raises_when_no_api_key(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path, api_key="")
        with pytest.raises(ServiceTeleportError, match="Mistral API key not set"):
            service._validate_config()

    def test_passes_when_api_key_set(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path, api_key="valid-key")
        service._validate_config()


class TestTeleportServiceCheckSupported:
    @pytest.fixture
    def service(self, tmp_path: Path) -> TeleportService:
        return _make_service(tmp_path)

    @pytest.mark.asyncio
    async def test_check_supported_calls_git_info(
        self, service: TeleportService
    ) -> None:
        service._git.get_info = AsyncMock(
            return_value=GitRepoInfo(
                remote_name="origin",
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
                "Teleport requires a git repository."
            )
        )
        with pytest.raises(ServiceTeleportNotSupportedError):
            await service.check_supported()


class TestTeleportServiceIsSupported:
    @pytest.fixture
    def service(self, tmp_path: Path) -> TeleportService:
        return _make_service(tmp_path)

    @pytest.mark.asyncio
    async def test_is_supported_returns_true(self, service: TeleportService) -> None:
        service._git.is_supported = AsyncMock(return_value=True)
        assert await service.is_supported() is True

    @pytest.mark.asyncio
    async def test_is_supported_returns_false(self, service: TeleportService) -> None:
        service._git.is_supported = AsyncMock(return_value=False)
        assert await service.is_supported() is False


class TestTeleportServiceExecute:
    def test_build_nuage_request_uses_project_id(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)

        request = service._build_nuage_request(
            prompt="test prompt",
            project_id="project-id",
            conversation_id="cli-conversation-id",
            git_info=GitRepoInfo(
                remote_name="origin",
                remote_url="https://github.com/owner/repo",
                owner="owner",
                repo="repo",
                branch="main",
                commit="abc123",
                diff="",
            ),
        )

        assert request.project_id == "project-id"
        assert request.conversation_id == "cli-conversation-id"
        assert (
            request.model_dump(mode="json", by_alias=True, exclude_none=True)[
                "projectId"
            ]
            == "project-id"
        )

    @pytest.mark.asyncio
    async def test_execute_happy_path(self, tmp_path: Path) -> None:
        seen_body: dict[str, object] | None = None
        seen_url: str | None = None

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen_body, seen_url
            seen_url = str(request.url)
            seen_body = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "sessionId": "controller-session-id",
                    "webSessionId": "web-session-id",
                    "projectId": "project-id",
                    "status": "running",
                    "url": TELEPORT_COMPLETE_URL,
                },
            )

        async with VibeAsyncHTTPClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            service = _make_service(
                tmp_path,
                vibe_code_sessions_base_url="https://chat.example.com",
                client=client,
            )
            service._git.fetch = AsyncMock()
            service._git.get_info = AsyncMock(
                return_value=GitRepoInfo(
                    remote_name="upstream",
                    remote_url="https://github.com/owner/repo",
                    owner="owner",
                    repo="repo",
                    branch="main",
                    commit="abc123",
                    diff="some local diff",
                )
            )
            service._git.is_commit_pushed = AsyncMock(return_value=True)
            service._git.is_branch_pushed = AsyncMock(return_value=True)

            events = [
                event
                async for event in service.execute(
                    "test prompt",
                    project_id="project-id",
                    message_context=TeleportMessageContext(
                        summary="Prior teleport context",
                        source=TeleportMessageContextSource(
                            entrypoint="acp", client_name="mistral-vibe-vscode"
                        ),
                    ),
                    conversation_id="cli-conversation-id",
                )
            ]

        assert isinstance(events[0], TeleportCheckingGitEvent)
        assert isinstance(events[1], TeleportStartingWorkflowEvent)
        assert isinstance(events[2], TeleportCompleteEvent)
        assert events[2].url == TELEPORT_COMPLETE_URL
        assert seen_url == f"https://chat.example.com{TELEPORT_SESSIONS_PATH}"
        assert seen_body is not None
        assert seen_body["projectId"] == "project-id"
        assert seen_body["conversationId"] == "cli-conversation-id"
        assert seen_body["message"] == {
            "role": "user",
            "parts": [{"type": "text", "text": "test prompt"}],
        }
        repos = seen_body["context"]["repositories"]
        assert len(repos) == 1
        assert repos[0]["repoUrl"] == "https://github.com/owner/repo"
        assert repos[0]["branch"] == "main"
        assert repos[0]["commitSha"] == "abc123"
        assert repos[0]["diff"]["format"] == "git-diff"
        assert repos[0]["diff"]["encoding"] == "base64"
        assert repos[0]["diff"]["compression"] == "zstd"
        assert len(repos[0]["diff"]["content"]) > 0
        assert seen_body["context"]["messageContext"] == {
            "summary": "Prior teleport context",
            "source": {
                "type": "teleport",
                "entrypoint": "acp",
                "clientName": "mistral-vibe-vscode",
            },
        }
        assert "idempotencyKey" in seen_body
        service._git.fetch.assert_awaited_once_with("upstream")
        service._git.is_commit_pushed.assert_awaited_once_with(
            "abc123", remote="upstream", fetch=False
        )
        service._git.is_branch_pushed.assert_awaited_once_with(
            remote="upstream", fetch=False
        )

    @pytest.mark.asyncio
    async def test_execute_requires_branch(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        service._git.fetch = AsyncMock()
        service._git.get_info = AsyncMock(
            return_value=GitRepoInfo(
                remote_name="origin",
                remote_url="https://github.com/owner/repo",
                owner="owner",
                repo="repo",
                branch=None,
                commit="abc123",
                diff="",
            )
        )

        with pytest.raises(ServiceTeleportError, match="checked-out branch"):
            async for _ in service.execute("test prompt", project_id="project-id"):
                pass

        service._git.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_rejects_empty_prompt(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        with pytest.raises(ServiceTeleportError, match="non-empty prompt"):
            async for _ in service.execute(""):
                pass

    @pytest.mark.asyncio
    async def test_execute_requires_project_id(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        service._git.get_info = AsyncMock()

        with pytest.raises(ServiceTeleportError, match="project id"):
            async for _ in service.execute("test prompt"):
                pass

        service._git.get_info.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_execute_push_confirmation_approved(self, tmp_path: Path) -> None:
        async with VibeAsyncHTTPClient(
            transport=httpx.MockTransport(_mock_handler())
        ) as client:
            service = _make_service(tmp_path, client=client)
            service._git.fetch = AsyncMock()
            service._git.get_info = AsyncMock(
                return_value=GitRepoInfo(
                    remote_name="github",
                    remote_url="https://github.com/owner/repo",
                    owner="owner",
                    repo="repo",
                    branch="main",
                    commit="abc123",
                    diff="",
                )
            )
            service._git.is_commit_pushed = AsyncMock(return_value=False)
            service._git.is_branch_pushed = AsyncMock(return_value=False)
            service._git.get_unpushed_commit_count = AsyncMock(return_value=2)
            service._git.push_current_branch = AsyncMock(return_value=True)

            gen = service.execute("test prompt", project_id="project-id")
            assert isinstance(await gen.asend(None), TeleportCheckingGitEvent)
            push_event = await gen.asend(None)
            assert isinstance(push_event, TeleportPushRequiredEvent)
            assert push_event.unpushed_count == 2
            assert push_event.branch_not_pushed is True
            assert isinstance(
                await gen.asend(TeleportPushResponseEvent(approved=True)),
                TeleportPushingEvent,
            )
            events = [event async for event in gen]

        service._git.get_unpushed_commit_count.assert_awaited_once_with("github")
        service._git.push_current_branch.assert_awaited_once_with("github")
        assert isinstance(events[0], TeleportStartingWorkflowEvent)
        assert isinstance(events[1], TeleportCompleteEvent)

    @pytest.mark.asyncio
    async def test_execute_push_confirmation_declined(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        service._git.fetch = AsyncMock()
        service._git.get_info = AsyncMock(
            return_value=GitRepoInfo(
                remote_name="origin",
                remote_url="https://github.com/owner/repo",
                owner="owner",
                repo="repo",
                branch="main",
                commit="abc123",
                diff="",
            )
        )
        service._git.is_commit_pushed = AsyncMock(return_value=False)
        service._git.is_branch_pushed = AsyncMock(return_value=True)
        service._git.get_unpushed_commit_count = AsyncMock(return_value=1)

        gen = service.execute("test prompt", project_id="project-id")
        await gen.asend(None)
        await gen.asend(None)

        with pytest.raises(ServiceTeleportError, match="Teleport cancelled"):
            await gen.asend(TeleportPushResponseEvent(approved=False))

    @pytest.mark.asyncio
    async def test_execute_clears_stale_project_link_off_event_loop(
        self, tmp_path: Path
    ) -> None:
        event_loop_thread = threading.get_ident()

        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="project not found")

        project_store = TrackingProjectsStore(tmp_path / "projects.toml")
        async with VibeAsyncHTTPClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            service = _make_service(
                tmp_path, client=client, project_store=project_store
            )
            service._git.fetch = AsyncMock()
            service._git.get_info = AsyncMock(
                return_value=GitRepoInfo(
                    remote_name="origin",
                    remote_url="https://github.com/owner/repo",
                    owner="owner",
                    repo="repo",
                    branch="main",
                    commit="abc123",
                    diff="",
                    repo_root=tmp_path,
                )
            )
            service._git.is_commit_pushed = AsyncMock(return_value=True)
            service._git.is_branch_pushed = AsyncMock(return_value=True)

            with pytest.raises(ServiceTeleportError, match="project not found"):
                async for _ in service.execute("test prompt", project_id="project-id"):
                    pass

        assert project_store.delete_remote_project_threads
        assert all(
            thread_id != event_loop_thread
            for thread_id in project_store.delete_remote_project_threads
        )


class TestTeleportServiceContextManager:
    @pytest.mark.asyncio
    async def test_creates_client_on_enter(self, tmp_path: Path) -> None:
        service = _make_service(tmp_path)
        assert service._client is None
        async with service:
            assert service._client is not None
            assert service._nuage_client_instance is not None
        assert service._client is None
