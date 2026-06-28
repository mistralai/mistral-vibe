from __future__ import annotations

import json

import httpx
import pytest

from tests.constants import TELEPORT_SESSIONS_PATH
from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.nuage import (
    NuageClient,
    NuageContext,
    NuageDiff,
    NuageMessage,
    NuageRepository,
    NuageRequest,
    NuageTextPart,
)


def _request() -> NuageRequest:
    return NuageRequest(
        idempotency_key="idem-1",
        message=NuageMessage(parts=[NuageTextPart(text="continue from here")]),
        context=NuageContext(
            repositories=[
                NuageRepository(repo_url="https://github.com/owner/repo", branch="main")
            ]
        ),
    )


@pytest.mark.asyncio
async def test_start_posts_nuage_request() -> None:
    seen_request: httpx.Request | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(
            200,
            json={
                "sessionId": "controller-session-id",
                "webSessionId": "web-session-id",
                "projectId": "project-id",
                "status": "running",
                "url": "https://chat.example.com/code/project-id/web-session-id",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        nuage = NuageClient("https://chat.example.com/", "api-key", client=client)
        response = await nuage.start(_request())

    assert seen_request is not None
    assert str(seen_request.url) == f"https://chat.example.com{TELEPORT_SESSIONS_PATH}"
    assert seen_request.headers["authorization"] == "Bearer api-key"
    assert seen_request.headers["content-type"] == "application/json"
    assert json.loads(seen_request.content) == {
        "project_name": "Vibe CLI",
        "source": "vibe_code_cli",
        "idempotencyKey": "idem-1",
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": "continue from here"}],
        },
        "context": {
            "repositories": [
                {"repoUrl": "https://github.com/owner/repo", "branch": "main"}
            ]
        },
    }
    assert response.nuage_session_id == "controller-session-id"
    assert response.nuage_web_session_id == "web-session-id"
    assert response.nuage_project_id == "project-id"
    assert response.status == "running"
    assert response.url == "https://chat.example.com/code/project-id/web-session-id"


@pytest.mark.asyncio
async def test_start_omits_empty_branch() -> None:
    seen_body: dict[str, object] | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_body
        seen_body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "sessionId": "controller-session-id",
                "webSessionId": "web-session-id",
                "projectId": "project-id",
                "status": "running",
                "url": "https://chat.example.com/code/project-id/web-session-id",
            },
        )

    request = NuageRequest(
        idempotency_key="idem-1",
        message=NuageMessage(parts=[NuageTextPart(text="prompt")]),
        context=NuageContext(
            repositories=[NuageRepository(repo_url="https://github.com/owner/repo")]
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        nuage = NuageClient("https://chat.example.com", "api-key", client=client)
        await nuage.start(request)

    assert seen_body == {
        "project_name": "Vibe CLI",
        "source": "vibe_code_cli",
        "idempotencyKey": "idem-1",
        "message": {"role": "user", "parts": [{"type": "text", "text": "prompt"}]},
        "context": {"repositories": [{"repoUrl": "https://github.com/owner/repo"}]},
    }


@pytest.mark.asyncio
async def test_start_serializes_commit_sha_and_diff() -> None:
    seen_body: dict[str, object] | None = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_body
        seen_body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "sessionId": "s",
                "webSessionId": "ws",
                "projectId": "p",
                "status": "running",
                "url": "https://chat.example.com/code/p/ws",
            },
        )

    request = NuageRequest(
        idempotency_key="idem-1",
        message=NuageMessage(parts=[NuageTextPart(text="prompt")]),
        context=NuageContext(
            repositories=[
                NuageRepository(
                    repo_url="https://github.com/owner/repo",
                    branch="main",
                    commit_sha="abc123",
                    diff=NuageDiff(
                        format="git-diff",
                        encoding="base64",
                        compression="zstd",
                        content="ZGlmZnM=",
                    ),
                )
            ]
        ),
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        nuage = NuageClient("https://chat.example.com", "api-key", client=client)
        await nuage.start(request)

    assert seen_body is not None
    repos = seen_body["context"]["repositories"]
    assert len(repos) == 1
    assert repos[0]["commitSha"] == "abc123"
    assert repos[0]["diff"] == {
        "format": "git-diff",
        "encoding": "base64",
        "compression": "zstd",
        "content": "ZGlmZnM=",
    }


@pytest.mark.asyncio
async def test_start_raises_for_unsuccessful_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="Unauthorized")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        nuage = NuageClient("https://chat.example.com", "api-key", client=client)
        with pytest.raises(ServiceTeleportError, match="status 401") as exc_info:
            await nuage.start(_request())

    assert exc_info.value.telemetry_details == {
        "failure_kind": "http_error",
        "http_status_code": 401,
    }


@pytest.mark.asyncio
async def test_start_raises_for_invalid_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"url": "https://chat.example.com/code/1/2"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        nuage = NuageClient("https://chat.example.com", "api-key", client=client)
        with pytest.raises(
            ServiceTeleportError, match="response was invalid"
        ) as exc_info:
            await nuage.start(_request())

    assert exc_info.value.telemetry_details == {
        "failure_kind": "invalid_schema",
        "http_status_code": 200,
    }


@pytest.mark.asyncio
async def test_start_raises_for_invalid_json_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="not-json", headers={"content-type": "text/plain"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        nuage = NuageClient("https://chat.example.com", "api-key", client=client)
        with pytest.raises(ServiceTeleportError, match="not valid JSON") as exc_info:
            await nuage.start(_request())

    assert exc_info.value.telemetry_details == {
        "failure_kind": "invalid_json",
        "http_status_code": 200,
    }
