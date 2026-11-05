from __future__ import annotations

import json

import httpx2
import pytest

from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialRequired,
    PlatformCredentialResult,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)
from vibe.core.vibe_code_project import VibeCodeProjectApiError, VibeCodeProjectClient
from vibe.utils.http import VibeAsyncHTTPClient


def _key() -> ApiKeyPlatformCredentialProvider:
    return ApiKeyPlatformCredentialProvider.from_api_key("api-key")


@pytest.mark.asyncio
async def test_context_manager_uses_vibe_http_client_by_default() -> None:
    async with VibeCodeProjectClient("https://chat.example.com", _key()) as client:
        assert isinstance(client._http_client, VibeAsyncHTTPClient)


@pytest.mark.asyncio
async def test_list_projects_fetches_first_page() -> None:
    seen_request: httpx2.Request | None = None

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal seen_request
        seen_request = request
        return httpx2.Response(
            200,
            json={
                "items": [
                    {
                        "id": "project-1",
                        "name": "Mistral Vibe",
                        "isReadOnly": False,
                        "repositories": [
                            {
                                "id": "repo-1",
                                "repoUrl": "https://github.com/mistralai/mistral-vibe.git",
                                "repoOwner": "mistralai",
                                "repoName": "mistral-vibe",
                                "defaultBranch": "main",
                            }
                        ],
                    }
                ],
                "nextCursor": "next-page",
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = VibeCodeProjectClient("https://chat.example.com/", _key(), client=http)
        page = await client.list_projects()

    assert seen_request is not None
    assert str(seen_request.url) == "https://chat.example.com/api/v1/code/projects"
    assert seen_request.headers["authorization"] == "Bearer api-key"
    assert page.next_cursor == "next-page"
    assert len(page.projects) == 1
    project = page.projects[0]
    assert project.project_id == "project-1"
    assert project.name == "Mistral Vibe"
    assert project.is_read_only is False
    assert (
        project.repositories[0].repo_url
        == "https://github.com/mistralai/mistral-vibe.git"
    )
    assert project.repositories[0].default_branch == "main"


@pytest.mark.asyncio
async def test_list_projects_sends_cursor_and_limit() -> None:
    seen_request: httpx2.Request | None = None

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal seen_request
        seen_request = request
        return httpx2.Response(200, json={"items": [], "nextCursor": None})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = VibeCodeProjectClient("https://chat.example.com", _key(), client=http)
        await client.list_projects(cursor="cursor-1", limit=100)

    assert seen_request is not None
    assert seen_request.url.params["cursor"] == "cursor-1"
    assert seen_request.url.params["limit"] == "100"


@pytest.mark.asyncio
async def test_list_projects_raises_on_http_error() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, text="forbidden")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = VibeCodeProjectClient("https://chat.example.com", _key(), client=http)
        with pytest.raises(VibeCodeProjectApiError, match="status 403"):
            await client.list_projects()


@pytest.mark.asyncio
async def test_create_project_posts_repo_linked_project() -> None:
    seen_request: httpx2.Request | None = None

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal seen_request
        seen_request = request
        return httpx2.Response(
            200,
            json={
                "id": "project-1",
                "name": "Mistral Vibe",
                "isReadOnly": False,
                "repositories": [
                    {
                        "repoUrl": "https://github.com/mistralai/mistral-vibe.git",
                        "defaultBranch": "main",
                    }
                ],
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = VibeCodeProjectClient("https://chat.example.com/", _key(), client=http)
        project = await client.create_project(
            name="Mistral Vibe",
            repo_url="https://github.com/mistralai/mistral-vibe.git",
            default_branch="main",
        )

    assert seen_request is not None
    assert str(seen_request.url) == "https://chat.example.com/api/v1/code/projects"
    assert seen_request.method == "POST"
    assert json.loads(seen_request.content) == {
        "name": "Mistral Vibe",
        "repositories": [
            {
                "repoUrl": "https://github.com/mistralai/mistral-vibe.git",
                "defaultBranch": "main",
            }
        ],
    }
    assert project.project_id == "project-1"
    assert project.repositories[0].default_branch == "main"


@pytest.mark.asyncio
async def test_create_project_posts_project_without_repository() -> None:
    seen_request: httpx2.Request | None = None

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal seen_request
        seen_request = request
        return httpx2.Response(
            200,
            json={
                "id": "project-1",
                "name": "Folder project",
                "isReadOnly": False,
                "repositories": [],
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = VibeCodeProjectClient("https://chat.example.com", _key(), client=http)
        project = await client.create_project(name="Folder project")

    assert seen_request is not None
    assert json.loads(seen_request.content) == {
        "name": "Folder project",
        "repositories": [],
    }
    assert project.repositories == ()


@pytest.mark.asyncio
async def test_create_project_raises_on_http_error() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, text="invalid repo")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = VibeCodeProjectClient("https://chat.example.com", _key(), client=http)
        with pytest.raises(VibeCodeProjectApiError, match="creation failed"):
            await client.create_project(
                name="Mistral Vibe",
                repo_url="https://github.com/mistralai/mistral-vibe.git",
                default_branch="main",
            )


@pytest.mark.asyncio
async def test_list_projects_raises_on_invalid_json() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"not-json")

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = VibeCodeProjectClient("https://chat.example.com", _key(), client=http)
        with pytest.raises(VibeCodeProjectApiError, match="not valid JSON"):
            await client.list_projects()


@pytest.mark.asyncio
async def test_list_projects_raises_on_invalid_schema() -> None:
    async def handler(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200, content=json.dumps({"items": [{"id": "missing-name"}]})
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = VibeCodeProjectClient("https://chat.example.com", _key(), client=http)
        with pytest.raises(VibeCodeProjectApiError, match="response was invalid"):
            await client.list_projects()


class _Credential:
    """A user token with tenant headers, or signed out when ``token`` is None."""

    def __init__(self, token: str | None) -> None:
        self.token = token

    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult:
        del target
        if self.token is None:
            return PlatformCredentialRequired(reason="missing")
        return PlatformCredentialSnapshot(
            kind="oidc_id_token",
            headers={
                "Authorization": f"Bearer {self.token}",
                "x-external-organization-id": "org-1",
            },
            cache_namespace="oidc_id_token:test",
            revision=self.token,
        )

    async def reject(
        self, *_args: object, **_kwargs: object
    ) -> PlatformCredentialResult:
        return PlatformCredentialRequired(reason="rejected")


@pytest.mark.asyncio
async def test_list_projects_sends_the_user_token_with_its_tenant() -> None:
    seen: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"items": [], "nextCursor": None})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = VibeCodeProjectClient(
            "https://chat.example.com", _Credential("id-token"), client=http
        )
        await client.list_projects()

    assert seen[0].headers["authorization"] == "Bearer id-token"
    assert seen[0].headers["x-external-organization-id"] == "org-1"


@pytest.mark.asyncio
async def test_list_projects_without_a_credential_sends_nothing() -> None:
    seen: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"items": [], "nextCursor": None})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = VibeCodeProjectClient(
            "https://chat.example.com", _Credential(None), client=http
        )
        with pytest.raises(VibeCodeProjectApiError, match="API key"):
            await client.list_projects()

    assert seen == []
