from __future__ import annotations

import json

import httpx
import pytest

from vibe.core.utils.http import VibeAsyncHTTPClient
from vibe.core.vibe_code_project import VibeCodeProjectApiError, VibeCodeProjectClient


@pytest.mark.asyncio
async def test_context_manager_uses_vibe_http_client_by_default() -> None:
    async with VibeCodeProjectClient("https://chat.example.com", "api-key") as client:
        assert isinstance(client._http_client, VibeAsyncHTTPClient)


@pytest.mark.asyncio
async def test_list_projects_fetches_first_page() -> None:
    seen_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(
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

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VibeCodeProjectClient(
            "https://chat.example.com/", "api-key", client=http
        )
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
    seen_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(200, json={"items": [], "nextCursor": None})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VibeCodeProjectClient(
            "https://chat.example.com", "api-key", client=http
        )
        await client.list_projects(cursor="cursor-1", limit=100)

    assert seen_request is not None
    assert seen_request.url.params["cursor"] == "cursor-1"
    assert seen_request.url.params["limit"] == "100"


@pytest.mark.asyncio
async def test_list_projects_raises_on_http_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VibeCodeProjectClient(
            "https://chat.example.com", "api-key", client=http
        )
        with pytest.raises(VibeCodeProjectApiError, match="status 403"):
            await client.list_projects()


@pytest.mark.asyncio
async def test_create_project_posts_repo_linked_project() -> None:
    seen_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(
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

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VibeCodeProjectClient(
            "https://chat.example.com/", "api-key", client=http
        )
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
async def test_create_project_raises_on_http_error() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="invalid repo")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VibeCodeProjectClient(
            "https://chat.example.com", "api-key", client=http
        )
        with pytest.raises(VibeCodeProjectApiError, match="creation failed"):
            await client.create_project(
                name="Mistral Vibe",
                repo_url="https://github.com/mistralai/mistral-vibe.git",
                default_branch="main",
            )


@pytest.mark.asyncio
async def test_list_projects_raises_on_invalid_json() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VibeCodeProjectClient(
            "https://chat.example.com", "api-key", client=http
        )
        with pytest.raises(VibeCodeProjectApiError, match="not valid JSON"):
            await client.list_projects()


@pytest.mark.asyncio
async def test_list_projects_raises_on_invalid_schema() -> None:
    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=json.dumps({"items": [{"id": "missing-name"}]})
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = VibeCodeProjectClient(
            "https://chat.example.com", "api-key", client=http
        )
        with pytest.raises(VibeCodeProjectApiError, match="response was invalid"):
            await client.list_projects()
