from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from vibe.core.auth import EncryptedPayload
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.nuage import (
    CreateLeChatThreadInput,
    GitRepoConfig,
    NuageClient,
    VibeNewSandbox,
    VibeSandboxConfig,
    WorkflowParams,
)


class TestNuageModels:
    def test_git_repo_config_defaults(self) -> None:
        config = GitRepoConfig(url="https://github.com/owner/repo.git")
        assert config.url == "https://github.com/owner/repo.git"
        assert config.branch is None
        assert config.commit is None

    def test_git_repo_config_with_values(self) -> None:
        config = GitRepoConfig(
            url="https://github.com/owner/repo.git", branch="main", commit="abc123"
        )
        assert config.branch == "main"
        assert config.commit == "abc123"

    def test_vibe_sandbox_config_defaults(self) -> None:
        config = VibeSandboxConfig()
        assert config.git_repo is None

    def test_vibe_new_sandbox_defaults(self) -> None:
        sandbox = VibeNewSandbox()
        assert sandbox.type == "new"
        assert sandbox.config.git_repo is None
        assert sandbox.teleported_diffs is None

    def test_workflow_params_serialization(self) -> None:
        params = WorkflowParams(
            prompt="test prompt",
            sandbox=VibeNewSandbox(
                config=VibeSandboxConfig(
                    git_repo=GitRepoConfig(
                        url="https://github.com/owner/repo.git",
                        branch="main",
                        commit="abc123",
                    )
                ),
                teleported_diffs=b"base64data",
            ),
        )
        data = params.model_dump()
        assert data["prompt"] == "test prompt"
        assert data["sandbox"]["type"] == "new"
        assert data["sandbox"]["config"]["git_repo"]["url"] == (
            "https://github.com/owner/repo.git"
        )
        assert data["sandbox"]["teleported_diffs"] == b"base64data"

    def test_create_le_chat_thread_input(self) -> None:
        input_data = CreateLeChatThreadInput(
            encrypted_api_key={"key": "value"},
            user_message="test message",
            project_name="test-project",
        )
        assert input_data.encrypted_api_key == {"key": "value"}
        assert input_data.user_message == "test message"
        assert input_data.project_name == "test-project"


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

        params = WorkflowParams(prompt="test", sandbox=VibeNewSandbox())
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

        params = WorkflowParams(prompt="test", sandbox=VibeNewSandbox())
        with pytest.raises(ServiceTeleportError, match="Nuage workflow trigger failed"):
            await nuage.start_workflow(params)

    @pytest.mark.asyncio
    async def test_start_workflow_unauthorized_hint(
        self, nuage: NuageClient, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.text = "Unauthorized"
        mock_client.post = AsyncMock(return_value=mock_response)

        params = WorkflowParams(prompt="test", sandbox=VibeNewSandbox())
        with pytest.raises(ServiceTeleportError, match="STAGING_MISTRAL_API_KEY"):
            await nuage.start_workflow(params)


class TestNuageClientSendGithubToken:
    @pytest.fixture
    def mock_client(self) -> MagicMock:
        return MagicMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def nuage(self, mock_client: MagicMock) -> NuageClient:
        return NuageClient(
            "https://api.example.com", "api-key", "workflow-id", client=mock_client
        )

    @pytest.mark.asyncio
    async def test_send_github_token_success(
        self,
        nuage: NuageClient,
        mock_client: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        query_response = MagicMock()
        query_response.is_success = True
        query_response.json.return_value = {
            "result": {
                "public_key": (
                    "-----BEGIN PUBLIC KEY-----\n"
                    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0Z3VS5JJcds3xfn/"
                    "ygWyf8TFXQNZ0XsLOqXB1Mi2+bKPFv1WfhECTxJ3c6SXl0p1sGyWTFxRV8u/"
                    "bKqYZ0E6VZ6YRTRPFiGq0kkONjVBFxOQ8Y0jeT0d9e0Y3E3MWDL8tQ0Nz9v8"
                    "5Y7gC8F1m/dEbBwPjCJQV0Dg0z3gZDO8RCG0GrBoLO0b+NNqL8FXPPDXQ1l4"
                    "FGnYM0gZ1rCU7Y/zTN1wI4sCQ0GJQPDA1hWB8KRJl5x0ZDXE3rRwT1E8c+Fn"
                    "ZFV1nN0C6zxF7GpVY3FVWXS4PA0FH+8C1+TnYgBL7xS0o+LF6PgjGT5F3CXD"
                    "BZmYSxKL+EsVVGT5EuYbJE9TxVwIDAQAB\n"
                    "-----END PUBLIC KEY-----"
                )
            }
        }

        signal_response = MagicMock()
        signal_response.is_success = True

        mock_client.post = AsyncMock(side_effect=[query_response, signal_response])

        encrypted = EncryptedPayload(
            encrypted_key="enc_key", nonce="nonce", ciphertext="cipher"
        )
        monkeypatch.setattr(
            "vibe.core.teleport.nuage.encrypt", lambda _token, _key: encrypted
        )

        await nuage.send_github_token("exec-123", "ghp_token")

        assert mock_client.post.call_count == 2

    @pytest.mark.asyncio
    async def test_query_public_key_failure(
        self, nuage: NuageClient, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.text = "Not found"
        mock_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(ServiceTeleportError, match="Failed to get public key"):
            await nuage._query_public_key("exec-123")

    @pytest.mark.asyncio
    async def test_signal_encrypted_token_failure(
        self, nuage: NuageClient, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.text = "Signal failed"
        mock_client.post = AsyncMock(return_value=mock_response)

        encrypted = EncryptedPayload(
            encrypted_key="enc_key", nonce="nonce", ciphertext="cipher"
        )
        with pytest.raises(ServiceTeleportError, match="Failed to send GitHub token"):
            await nuage._signal_encrypted_token("exec-123", encrypted)


class TestNuageClientCreateLeChatThread:
    @pytest.fixture
    def mock_client(self) -> MagicMock:
        return MagicMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def nuage(self, mock_client: MagicMock) -> NuageClient:
        return NuageClient(
            "https://api.example.com", "api-key", "workflow-id", client=mock_client
        )

    @pytest.mark.asyncio
    async def test_create_le_chat_thread_success(
        self,
        nuage: NuageClient,
        mock_client: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        query_response = MagicMock()
        query_response.is_success = True
        query_response.json.return_value = {
            "result": {
                "public_key": (
                    "-----BEGIN PUBLIC KEY-----\n"
                    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0Z3VS5JJcds3xfn/"
                    "ygWyf8TFXQNZ0XsLOqXB1Mi2+bKPFv1WfhECTxJ3c6SXl0p1sGyWTFxRV8u/"
                    "bKqYZ0E6VZ6YRTRPFiGq0kkONjVBFxOQ8Y0jeT0d9e0Y3E3MWDL8tQ0Nz9v8"
                    "5Y7gC8F1m/dEbBwPjCJQV0Dg0z3gZDO8RCG0GrBoLO0b+NNqL8FXPPDXQ1l4"
                    "FGnYM0gZ1rCU7Y/zTN1wI4sCQ0GJQPDA1hWB8KRJl5x0ZDXE3rRwT1E8c+Fn"
                    "ZFV1nN0C6zxF7GpVY3FVWXS4PA0FH+8C1+TnYgBL7xS0o+LF6PgjGT5F3CXD"
                    "BZmYSxKL+EsVVGT5EuYbJE9TxVwIDAQAB\n"
                    "-----END PUBLIC KEY-----"
                )
            }
        }

        update_response = MagicMock()
        update_response.is_success = True
        update_response.json.return_value = {
            "result": {"chat_url": "https://chat.example.com/thread/123"}
        }

        mock_client.post = AsyncMock(side_effect=[query_response, update_response])

        encrypted = EncryptedPayload(
            encrypted_key="enc_key", nonce="nonce", ciphertext="cipher"
        )
        monkeypatch.setattr(
            "vibe.core.teleport.nuage.encrypt", lambda _token, _key: encrypted
        )

        url = await nuage.create_le_chat_thread("exec-123", "test message")
        assert url == "https://chat.example.com/thread/123"

    @pytest.mark.asyncio
    async def test_create_le_chat_thread_failure(
        self,
        nuage: NuageClient,
        mock_client: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        query_response = MagicMock()
        query_response.is_success = True
        query_response.json.return_value = {
            "result": {
                "public_key": (
                    "-----BEGIN PUBLIC KEY-----\n"
                    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA0Z3VS5JJcds3xfn/"
                    "ygWyf8TFXQNZ0XsLOqXB1Mi2+bKPFv1WfhECTxJ3c6SXl0p1sGyWTFxRV8u/"
                    "bKqYZ0E6VZ6YRTRPFiGq0kkONjVBFxOQ8Y0jeT0d9e0Y3E3MWDL8tQ0Nz9v8"
                    "5Y7gC8F1m/dEbBwPjCJQV0Dg0z3gZDO8RCG0GrBoLO0b+NNqL8FXPPDXQ1l4"
                    "FGnYM0gZ1rCU7Y/zTN1wI4sCQ0GJQPDA1hWB8KRJl5x0ZDXE3rRwT1E8c+Fn"
                    "ZFV1nN0C6zxF7GpVY3FVWXS4PA0FH+8C1+TnYgBL7xS0o+LF6PgjGT5F3CXD"
                    "BZmYSxKL+EsVVGT5EuYbJE9TxVwIDAQAB\n"
                    "-----END PUBLIC KEY-----"
                )
            }
        }

        update_response = MagicMock()
        update_response.is_success = False
        update_response.text = "Failed to create thread"

        mock_client.post = AsyncMock(side_effect=[query_response, update_response])

        encrypted = EncryptedPayload(
            encrypted_key="enc_key", nonce="nonce", ciphertext="cipher"
        )
        monkeypatch.setattr(
            "vibe.core.teleport.nuage.encrypt", lambda _token, _key: encrypted
        )

        with pytest.raises(
            ServiceTeleportError, match="Failed to create Le Chat thread"
        ):
            await nuage.create_le_chat_thread("exec-123", "test message")


class TestNuageClientHeaders:
    def test_headers_include_auth(self) -> None:
        nuage = NuageClient("https://api.example.com", "test-api-key", "workflow-id")
        headers = nuage._headers()
        assert headers["Authorization"] == "Bearer test-api-key"
        assert headers["Content-Type"] == "application/json"
