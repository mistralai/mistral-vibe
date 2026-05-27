from __future__ import annotations

import types
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.utils.http import build_ssl_context


class ExperimentalNuageTextPart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str = "text"
    text: str


class ExperimentalNuageMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str = "user"
    parts: list[ExperimentalNuageTextPart]


class ExperimentalNuageDiff(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["git-diff"] = "git-diff"
    encoding: Literal["base64"] = "base64"
    compression: Literal["zstd"] = "zstd"
    content: str


class ExperimentalNuageRepository(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_url: str = Field(serialization_alias="repoUrl")
    branch: str | None = None
    commit_sha: str | None = Field(default=None, serialization_alias="commitSha")
    diff: ExperimentalNuageDiff | None = None


class ExperimentalNuageContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repositories: list[ExperimentalNuageRepository]


class ExperimentalNuageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_name: str = Field(default="Vibe CLI", serialization_alias="project_name")
    source: str = "vibe_code_cli"
    idempotency_key: str = Field(serialization_alias="idempotencyKey")
    message: ExperimentalNuageMessage
    context: ExperimentalNuageContext


class ExperimentalNuageResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    nuage_session_id: str = Field(validation_alias="sessionId")
    nuage_web_session_id: str = Field(validation_alias="webSessionId")
    nuage_project_id: str = Field(validation_alias="projectId")
    status: str
    url: str


class ExperimentalNuageClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._client = client
        self._owns_client = client is None
        self._timeout = timeout

    async def __aenter__(self) -> ExperimentalNuageClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout), verify=build_ssl_context()
            )
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
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout), verify=build_ssl_context()
            )
            self._owns_client = True
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def start(
        self, request: ExperimentalNuageRequest
    ) -> ExperimentalNuageResponse:
        response = await self._http_client.post(
            f"{self._base_url}/api/v1/code/sessions",
            headers=self._headers(),
            json=request.model_dump(mode="json", by_alias=True, exclude_none=True),
        )
        if not response.is_success:
            raise ServiceTeleportError(f"Vibe Code Nuage start failed: {response.text}")

        try:
            return ExperimentalNuageResponse.model_validate(response.json())
        except (ValueError, ValidationError) as e:
            raise ServiceTeleportError("Vibe Code Nuage response was invalid") from e
