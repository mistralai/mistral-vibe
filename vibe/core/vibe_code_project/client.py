from __future__ import annotations

from dataclasses import dataclass
import types

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vibe.core.utils.http import VibeAsyncHTTPClient, build_ssl_context
from vibe.core.vibe_code_project.selection import ProjectRepository, VibeCodeProject


class VibeCodeProjectApiError(Exception):
    pass


@dataclass(frozen=True)
class VibeCodeProjectPage:
    projects: list[VibeCodeProject]
    next_cursor: str | None


class _ProjectRepositoryResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    repo_url: str = Field(validation_alias="repoUrl")
    default_branch: str | None = Field(default=None, validation_alias="defaultBranch")

    def to_domain(self) -> ProjectRepository:
        return ProjectRepository(
            repo_url=self.repo_url, default_branch=self.default_branch
        )


class _ProjectResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project_id: str = Field(validation_alias="id")
    name: str
    repositories: list[_ProjectRepositoryResponse] = Field(default_factory=list)
    is_read_only: bool = Field(default=False, validation_alias="isReadOnly")

    def to_domain(self) -> VibeCodeProject:
        return VibeCodeProject(
            project_id=self.project_id,
            name=self.name,
            repositories=tuple(
                repository.to_domain() for repository in self.repositories
            ),
            is_read_only=self.is_read_only,
        )


class _ProjectListResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[_ProjectResponse]
    next_cursor: str | None = Field(default=None, validation_alias="nextCursor")

    def to_domain(self) -> VibeCodeProjectPage:
        return VibeCodeProjectPage(
            projects=[item.to_domain() for item in self.items],
            next_cursor=self.next_cursor,
        )


class VibeCodeProjectClient:
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

    async def __aenter__(self) -> VibeCodeProjectClient:
        if self._client is None:
            self._client = VibeAsyncHTTPClient(
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
            self._client = VibeAsyncHTTPClient(
                timeout=httpx.Timeout(self._timeout), verify=build_ssl_context()
            )
            self._owns_client = True
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    async def list_projects(
        self, cursor: str | None = None, limit: int | None = None
    ) -> VibeCodeProjectPage:
        params: dict[str, str | int] = {}
        if cursor:
            params["cursor"] = cursor
        if limit is not None:
            params["limit"] = limit
        try:
            response = await self._http_client.get(
                f"{self._base_url}/api/v1/code/projects",
                headers=self._headers(),
                params=params or None,
            )
        except httpx.RequestError as e:
            raise VibeCodeProjectApiError(
                "Failed to fetch Vibe Code Web projects."
            ) from e

        if not response.is_success:
            raise VibeCodeProjectApiError(
                f"Vibe Code Web projects request failed "
                f"(status {response.status_code}): {response.text}"
            )

        try:
            return _ProjectListResponse.model_validate(response.json()).to_domain()
        except ValidationError as e:
            raise VibeCodeProjectApiError(
                "Vibe Code Web projects response was invalid."
            ) from e
        except ValueError as e:
            raise VibeCodeProjectApiError(
                "Vibe Code Web projects response was not valid JSON."
            ) from e

    async def create_project(
        self, *, name: str, repo_url: str, default_branch: str
    ) -> VibeCodeProject:
        try:
            response = await self._http_client.post(
                f"{self._base_url}/api/v1/code/projects",
                headers=self._headers(),
                json={
                    "name": name,
                    "repositories": [
                        {"repoUrl": repo_url, "defaultBranch": default_branch}
                    ],
                },
            )
        except httpx.RequestError as e:
            raise VibeCodeProjectApiError(
                "Failed to create Vibe Code Web project."
            ) from e

        if not response.is_success:
            raise VibeCodeProjectApiError(
                f"Vibe Code Web project creation failed "
                f"(status {response.status_code}): {response.text}"
            )

        try:
            return _ProjectResponse.model_validate(response.json()).to_domain()
        except ValidationError as e:
            raise VibeCodeProjectApiError(
                "Vibe Code Web project creation response was invalid."
            ) from e
        except ValueError as e:
            raise VibeCodeProjectApiError(
                "Vibe Code Web project creation response was not valid JSON."
            ) from e
