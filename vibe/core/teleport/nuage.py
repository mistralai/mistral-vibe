from __future__ import annotations

from dataclasses import asdict
import types
from typing import Any

import httpx
from pydantic import BaseModel, Field

from vibe.core.auth import EncryptedPayload, encrypt
from vibe.core.teleport.errors import ServiceTeleportError


class GitRepoConfig(BaseModel):
    url: str
    branch: str | None = None
    commit: str | None = None


class VibeSandboxConfig(BaseModel):
    git_repo: GitRepoConfig | None = None


class VibeNewSandbox(BaseModel):
    type: str = "new"
    config: VibeSandboxConfig = Field(default_factory=VibeSandboxConfig)
    teleported_diffs: bytes | None = None


class TeleportSession(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowParams(BaseModel):
    prompt: str
    sandbox: VibeNewSandbox
    session: TeleportSession | None = None


class WorkflowExecuteResponse(BaseModel):
    execution_id: str


class PublicKeyResult(BaseModel):
    public_key: str


class QueryResponse(BaseModel):
    result: PublicKeyResult


class CreateLeChatThreadInput(BaseModel):
    encrypted_api_key: dict[str, str]
    user_message: str
    project_name: str | None = None


class CreateLeChatThreadOutput(BaseModel):
    chat_url: str


class UpdateResponse(BaseModel):
    result: CreateLeChatThreadOutput


class NuageClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        workflow_id: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._workflow_id = workflow_id
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    async def __aenter__(self) -> NuageClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if self._owns_client and self._client:
            await self._client.aclose()
            self._client = None

    @property
    def _http_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(self._timeout))
            self._owns_client = True
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def start_workflow(self, params: WorkflowParams) -> str:
        response = await self._http_client.post(
            f"{self._base_url}/v1/workflows/{self._workflow_id}/execute",
            headers=self._headers(),
            json={"input": params.model_dump(mode="json")},
        )
        if not response.is_success:
            error_msg = f"Nuage workflow trigger failed: {response.text}"
            # TODO(vibe-nuage): remove this once prod has shared vibe-nuage workers
            if "Unauthorized" in response.text or "unauthorized" in response.text:
                error_msg += (
                    "\n\nHint: This version uses Mistral staging environment. "
                    "Set STAGING_MISTRAL_API_KEY from https://console.globalaegis.net/"
                )
            raise ServiceTeleportError(error_msg)
        result = WorkflowExecuteResponse.model_validate(response.json())
        return result.execution_id

    async def send_github_token(self, execution_id: str, token: str) -> None:
        public_key_pem = await self._query_public_key(execution_id)
        encrypted = encrypt(token, public_key_pem)
        await self._signal_encrypted_token(execution_id, encrypted)

    async def _query_public_key(self, execution_id: str) -> bytes:
        response = await self._http_client.post(
            f"{self._base_url}/v1/workflows/executions/{execution_id}/queries",
            headers=self._headers(),
            json={"name": "get_public_key", "input": {}},
        )
        if not response.is_success:
            raise ServiceTeleportError(f"Failed to get public key: {response.text}")

        result = QueryResponse.model_validate(response.json())
        return result.result.public_key.encode("utf-8")

    async def _signal_encrypted_token(
        self, execution_id: str, encrypted: EncryptedPayload
    ) -> None:
        response = await self._http_client.post(
            f"{self._base_url}/v1/workflows/executions/{execution_id}/signals",
            headers=self._headers(),
            json={"name": "github_token", "input": {"payload": asdict(encrypted)}},
        )
        if not response.is_success:
            raise ServiceTeleportError(f"Failed to send GitHub token: {response.text}")

    async def create_le_chat_thread(
        self, execution_id: str, user_message: str, project_name: str | None = None
    ) -> str:
        public_key_pem = await self._query_public_key(execution_id)
        encrypted = encrypt(self._api_key, public_key_pem)
        input_data = CreateLeChatThreadInput(
            encrypted_api_key={
                k: v for k, v in asdict(encrypted).items() if v is not None
            },
            user_message=user_message,
            project_name=project_name,
        )
        response = await self._http_client.post(
            f"{self._base_url}/v1/workflows/executions/{execution_id}/updates",
            headers=self._headers(),
            json={"name": "create_le_chat_thread", "input": input_data.model_dump()},
        )
        if not response.is_success:
            raise ServiceTeleportError(
                f"Failed to create Le Chat thread: {response.text}"
            )
        result = UpdateResponse.model_validate(response.json())
        return result.result.chat_url
