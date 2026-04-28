from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.nuage import (
    ChatAssistantParams,
    GitHubParams,
    NuageClient,
    WorkflowIntegrations,
    WorkflowParams,
)


class TestNuageModels:
    def test_github_params_defaults(self) -> None:
        params = GitHubParams(repo="owner/repo")
        assert params.repo == "owner/repo"
        assert params.branch is None
        assert params.commit is None
        assert params.pr_number is None
        assert params.teleported_diffs is None

    def test_github_params_with_values(self) -> None:
        params = GitHubParams(
            repo="owner/repo",
            branch="main",
            commit="abc123",
            pr_number=42,
            teleported_diffs=b"base64data",
        )
        assert params.repo == "owner/repo"
        assert params.branch == "main"
        assert params.commit == "abc123"
        assert params.pr_number == 42
        assert params.teleported_diffs == b"base64data"

    def test_chat_assistant_params(self) -> None:
        params = ChatAssistantParams(
            user_message="test message", project_name="test-project"
        )
        assert params.user_message == "test message"
        assert params.project_name == "test-project"

    def test_workflow_integrations(self) -> None:
        integrations = WorkflowIntegrations(
            github=GitHubParams(repo="owner/repo"),
            chat_assistant=ChatAssistantParams(user_message="test"),
        )
        assert integrations.github is not None
        assert integrations.chat_assistant is not None

    def test_workflow_params_serialization(self) -> None:
        params = WorkflowParams(
            prompt="test prompt",
            integrations=WorkflowIntegrations(
                github=GitHubParams(
                    repo="owner/repo",
                    branch="main",
                    commit="abc123",
                    pr_number=42,
                    teleported_diffs=b"base64data",
                ),
                chat_assistant=ChatAssistantParams(user_message="test"),
            ),
        )
        data = params.model_dump()
        assert data["prompt"] == "test prompt"
        assert data["integrations"]["github"]["repo"] == "owner/repo"
        assert data["integrations"]["github"]["pr_number"] == 42
        assert data["integrations"]["github"]["teleported_diffs"] == b"base64data"


class TestNuageClientContextManager:
    @pytest.mark.asyncio
    async def test_creates_client_on_enter(self) -> None:
        nuage = NuageClient("https://api.example.com", "api-key", "workflow-id")
        assert nuage._client is None
        async with nuage:
            assert nuage._client is not None
        assert nuage._client is None

    @pytest.mark.asyncio
    async def test_uses_provided_client(self) -> None:
        external_client = httpx.AsyncClient()
        nuage = NuageClient(
            "https://api.example.com", "api-key", "workflow-id", client=external_client
        )
        async with nuage:
            assert nuage._client is external_client
        assert nuage._client is external_client
        await external_client.aclose()


class TestNuageClientStartWorkflow:
    @pytest.fixture
    def mock_client(self) -> MagicMock:
        client = MagicMock(spec=httpx.AsyncClient)
        return client

    @pytest.fixture
    def nuage(self, mock_client: MagicMock) -> NuageClient:
        return NuageClient(
            "https://api.example.com", "api-key", "workflow-id", client=mock_client
        )

    @pytest.mark.asyncio
    async def test_start_workflow_success(
        self, nuage: NuageClient, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"execution_id": "exec-123"}
        mock_client.post = AsyncMock(return_value=mock_response)

        params = WorkflowParams(prompt="test", integrations=WorkflowIntegrations())
        execution_id = await nuage.start_workflow(params)

        assert execution_id == "exec-123"
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "workflows/workflow-id/execute" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_start_workflow_failure(
        self, nuage: NuageClient, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.text = "Internal Server Error"
        mock_client.post = AsyncMock(return_value=mock_response)

        params = WorkflowParams(prompt="test", integrations=WorkflowIntegrations())
        with pytest.raises(
            ServiceTeleportError, match="Vibe Code workflow trigger failed"
        ):
            await nuage.start_workflow(params)


class TestNuageClientGetGitHubIntegration:
    @pytest.fixture
    def mock_client(self) -> MagicMock:
        return MagicMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def nuage(self, mock_client: MagicMock) -> NuageClient:
        return NuageClient(
            "https://api.example.com", "api-key", "workflow-id", client=mock_client
        )

    @pytest.mark.asyncio
    async def test_get_github_integration_connected(
        self, nuage: NuageClient, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "result": {"status": "connected", "oauth_url": None, "error": None}
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await nuage.get_github_integration("exec-123")

        assert result.connected is True
        assert result.oauth_url is None

    @pytest.mark.asyncio
    async def test_get_github_integration_waiting_for_oauth(
        self, nuage: NuageClient, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "result": {
                "status": "waiting_for_oauth",
                "oauth_url": "https://github.com/login/oauth",
                "error": None,
            }
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        result = await nuage.get_github_integration("exec-123")

        assert result.connected is False
        assert result.oauth_url == "https://github.com/login/oauth"

    @pytest.mark.asyncio
    async def test_get_github_integration_failure(
        self, nuage: NuageClient, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.text = "Not found"
        mock_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(
            ServiceTeleportError, match="Failed to get GitHub integration"
        ):
            await nuage.get_github_integration("exec-123")


class TestNuageClientGetChatAssistantUrl:
    @pytest.fixture
    def mock_client(self) -> MagicMock:
        return MagicMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def nuage(self, mock_client: MagicMock) -> NuageClient:
        return NuageClient(
            "https://api.example.com", "api-key", "workflow-id", client=mock_client
        )

    @pytest.mark.asyncio
    async def test_get_chat_assistant_url_success(
        self, nuage: NuageClient, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "result": {"chat_url": "https://chat.example.com/thread/123"}
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        url = await nuage.get_chat_assistant_url("exec-123")
        assert url == "https://chat.example.com/thread/123"

    @pytest.mark.asyncio
    async def test_get_chat_assistant_url_none(
        self, nuage: NuageClient, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {"result": {"chat_url": None}}
        mock_client.post = AsyncMock(return_value=mock_response)

        url = await nuage.get_chat_assistant_url("exec-123")
        assert url is None

    @pytest.mark.asyncio
    async def test_get_chat_assistant_url_failure(
        self, nuage: NuageClient, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.text = "Failed"
        mock_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(
            ServiceTeleportError, match="Failed to get chat assistant integration"
        ):
            await nuage.get_chat_assistant_url("exec-123")


class TestNuageClientHeaders:
    def test_headers_include_auth(self) -> None:
        nuage = NuageClient("https://api.example.com", "test-api-key", "workflow-id")
        headers = nuage._headers()
        assert headers["Authorization"] == "Bearer test-api-key"
        assert headers["Content-Type"] == "application/json"
