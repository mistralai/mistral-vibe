from __future__ import annotations

import asyncio
from enum import StrEnum, auto
import time
import types
from typing import Any

import httpx
from pydantic import BaseModel, Field, ValidationError

from vibe.core.teleport.errors import ServiceTeleportError


class GitHubParams(BaseModel):
    repo: str | None = None
    branch: str | None = None
    commit: str | None = None
    pr_number: int | None = None
    teleported_diffs: bytes | None = None


class ChatAssistantParams(BaseModel):
    create_thread: bool = False
    user_message: str | None = None
    project_name: str | None = None


class TeleportSession(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)
    messages: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowIntegrations(BaseModel):
    github: GitHubParams | None = None
    chat_assistant: ChatAssistantParams | None = None


class VibeAgent(BaseModel):
    polymorphic_type: str = "vibe_agent"
    name: str = "vibe-agent"
    vibe_config: dict[str, Any] | None = None
    session: TeleportSession | None = None


class WorkflowConfig(BaseModel):
    agent: VibeAgent = Field(default_factory=VibeAgent)


class TextChunk(BaseModel):
    type: str = "text"
    text: str


class WorkflowParams(BaseModel):
    prompt: str
    message: list[TextChunk] | None = None
    config: WorkflowConfig = Field(default_factory=WorkflowConfig)
    integrations: WorkflowIntegrations = Field(default_factory=WorkflowIntegrations)


class WorkflowExecuteResponse(BaseModel):
    execution_id: str


class GitHubStatus(StrEnum):
    PENDING = auto()
    WAITING_FOR_OAUTH = auto()
    CONNECTED = auto()
    OAUTH_TIMEOUT = auto()
    ERROR = auto()


class GitHubPublicData(BaseModel):
    status: GitHubStatus
    oauth_url: str | None = None
    error: str | None = None
    working_branch: str | None = None
    repo: str | None = None

    @property
    def connected(self) -> bool:
        return self.status == GitHubStatus.CONNECTED

    @property
    def is_error(self) -> bool:
        return self.status in {GitHubStatus.OAUTH_TIMEOUT, GitHubStatus.ERROR}


class ChatAssistantPublicData(BaseModel):
    chat_url: str | None = None


class GetChatAssistantIntegrationResponse(BaseModel):
    result: ChatAssistantPublicData


class GetGitHubIntegrationResponse(BaseModel):
    result: GitHubPublicData


class NuageClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        workflow_id: str,
        *,
        task_queue: str | None = None,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._workflow_id = workflow_id
        self._task_queue = task_queue
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
            json={
                "input": params.model_dump(mode="json"),
                "task_queue": self._task_queue,
            },
        )
        if not response.is_success:
            error_msg = f"Vibe Code workflow trigger failed: {response.text}"
            raise ServiceTeleportError(error_msg)
        result = WorkflowExecuteResponse.model_validate(response.json())
        return result.execution_id

    async def get_github_integration(self, execution_id: str) -> GitHubPublicData:
        response = await self._http_client.post(
            f"{self._base_url}/v1/workflows/executions/{execution_id}/updates",
            headers=self._headers(),
            json={"name": "get_integration", "input": {"integration_id": "github"}},
        )
        if not response.is_success:
            raise ServiceTeleportError(
                f"Failed to get GitHub integration: {response.text}"
            )
        try:
            result = GetGitHubIntegrationResponse.model_validate(response.json())
        except ValidationError as e:
            data = response.json()
            error = data.get("result", {}).get("error")
            status = data.get("result", {}).get("status")
            raise ServiceTeleportError(
                f"GitHub integration error: {error or status}"
            ) from e
        return result.result

    async def wait_for_github_connection(
        self, execution_id: str, timeout: float = 600.0, interval: float = 2.0
    ) -> GitHubPublicData:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            github_data = await self.get_github_integration(execution_id)
            if github_data.connected:
                return github_data
            if github_data.is_error:
                raise ServiceTeleportError(
                    github_data.error
                    or f"GitHub integration failed: {github_data.status.value}"
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(interval, remaining))
        raise ServiceTeleportError("GitHub connection timed out")

    async def get_chat_assistant_url(self, execution_id: str) -> str | None:
        response = await self._http_client.post(
            f"{self._base_url}/v1/workflows/executions/{execution_id}/updates",
            headers=self._headers(),
            json={
                "name": "get_integration",
                "input": {"integration_id": "chat_assistant"},
            },
        )
        if not response.is_success:
            raise ServiceTeleportError(
                f"Failed to get chat assistant integration: {response.text}"
            )
        result = GetChatAssistantIntegrationResponse.model_validate(response.json())
        return result.result.chat_url
