from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from vibe.cli.update_notifier.github_version_update_gateway import (
    GitHubVersionUpdateGateway,
)
from vibe.cli.update_notifier.version_update_gateway import (
    VersionUpdateGatewayCause,
    VersionUpdateGatewayError,
)

Handler = Callable[[httpx.Request], httpx.Response]

GITHUB_API_URL = "https://api.github.com"


def _raise_connect_timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("boom", request=request)


@pytest.mark.asyncio
async def test_retrieves_latest_version_when_available() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("Authorization") == "Bearer token"
        return httpx.Response(
            status_code=httpx.codes.OK,
            json=[{"tag_name": "v1.2.3", "prerelease": False, "draft": False}],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url=GITHUB_API_URL
    ) as client:
        notifier = GitHubVersionUpdateGateway(
            "owner", "repo", token="token", client=client
        )
        update = await notifier.fetch_update()

    assert update is not None
    assert update.latest_version == "1.2.3"


@pytest.mark.asyncio
async def test_strips_uppercase_prefix_from_tag_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=httpx.codes.OK,
            json=[{"tag_name": "V0.9.0", "prerelease": False, "draft": False}],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url=GITHUB_API_URL
    ) as client:
        notifier = GitHubVersionUpdateGateway("owner", "repo", client=client)
        update = await notifier.fetch_update()

    assert update is not None
    assert update.latest_version == "0.9.0"


@pytest.mark.asyncio
async def test_considers_no_update_available_when_no_releases_are_found() -> None:
    """If the repository cannot be accessed (e.g. invalid token), the response will be 404.
    But using API 'releases/latest', if no release has been created, the response will ALSO be 404.

    This test ensures that we consider no update available when no releases are found.
    (And this is why we are using "releases" with a per_page=1 parameter, instead of "releases/latest")
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=httpx.codes.OK, json=[])

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url=GITHUB_API_URL
    ) as client:
        notifier = GitHubVersionUpdateGateway("owner", "repo", client=client)
        update = await notifier.fetch_update()

    assert update is None


@pytest.mark.asyncio
async def test_considers_no_update_available_when_only_drafts_and_prereleases_are_found() -> (
    None
):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=httpx.codes.OK,
            json=[
                {"tag_name": "v2.0.0-beta", "prerelease": True, "draft": False},
                {"tag_name": "v2.0.0", "prerelease": False, "draft": True},
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url=GITHUB_API_URL
    ) as client:
        notifier = GitHubVersionUpdateGateway("owner", "repo", client=client)
        update = await notifier.fetch_update()

    assert update is None


@pytest.mark.asyncio
async def test_picks_the_most_recently_published_non_prerelease_and_non_draft() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code=httpx.codes.OK,
            json=[
                {
                    "tag_name": "v2.0.0-beta",
                    "prerelease": True,
                    "draft": False,
                    "published_at": "2025-10-25T112:00:00Z",
                },
                {
                    "tag_name": "v2.0.0",
                    "prerelease": False,
                    "draft": True,
                    "published_at": "2025-10-26T112:00:00Z",
                },
                {
                    "tag_name": "v1.12.455",
                    "prerelease": False,
                    "draft": False,
                    "published_at": "2025-11-02T112:00:00Z",
                },
                {
                    "tag_name": "1.12.400",
                    "prerelease": False,
                    "draft": False,
                    "published_at": "2025-11-10T112:00:00Z",
                },
                {
                    "tag_name": "1.12.300",
                    "prerelease": False,
                    "draft": False,
                    "published_at": "2025-11-11T112:00:00Z",
                },
            ],
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url=GITHUB_API_URL
    ) as client:
        notifier = GitHubVersionUpdateGateway("owner", "repo", client=client)
        update = await notifier.fetch_update()

    assert update is not None
    assert update.latest_version == "1.12.300"


@pytest.mark.parametrize(
    "payload",
    [
        [{"tag_name": "v2.0.0-beta", "prerelease": True, "draft": False}],
        [{"tag_name": "v2.0.0", "prerelease": False, "draft": True}],
    ],
)
@pytest.mark.asyncio
async def test_ignores_draft_releases_and_prereleases(
    payload: dict[str, object],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code=httpx.codes.OK, json=payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url=GITHUB_API_URL
    ) as client:
        notifier = GitHubVersionUpdateGateway("owner", "repo", client=client)
        update = await notifier.fetch_update()

    assert update is None


@pytest.mark.parametrize(
    ("handler", "expected_cause", "expected_custom_message"),
    [
        (
            lambda _: httpx.Response(status_code=httpx.codes.NOT_FOUND),
            VersionUpdateGatewayCause.NOT_FOUND,
            "Unable to fetch the GitHub releases. Did you export a GITHUB_TOKEN environment variable?",
        ),
        (
            lambda _: httpx.Response(
                status_code=httpx.codes.FORBIDDEN,
                headers={"X-RateLimit-Remaining": "0"},
            ),
            VersionUpdateGatewayCause.TOO_MANY_REQUESTS,
            None,
        ),
        (
            lambda _: httpx.Response(status_code=httpx.codes.TOO_MANY_REQUESTS),
            VersionUpdateGatewayCause.TOO_MANY_REQUESTS,
            None,
        ),
        (
            lambda _: httpx.Response(status_code=httpx.codes.FORBIDDEN),
            VersionUpdateGatewayCause.FORBIDDEN,
            None,
        ),
        (
            lambda _: httpx.Response(status_code=httpx.codes.INTERNAL_SERVER_ERROR),
            VersionUpdateGatewayCause.ERROR_RESPONSE,
            None,
        ),
        (
            lambda _: httpx.Response(status_code=httpx.codes.OK, text="not json"),
            VersionUpdateGatewayCause.INVALID_RESPONSE,
            None,
        ),
        (_raise_connect_timeout, VersionUpdateGatewayCause.REQUEST_FAILED, None),
    ],
    ids=[
        "not_found",
        "rate_limit_header",
        "rate_limit_status",
        "forbidden",
        "error_response",
        "invalid_json",
        "request_error",
    ],
)
@pytest.mark.asyncio
async def test_retrieves_nothing_when_fetching_update_fails(
    handler: Handler,
    expected_cause: VersionUpdateGatewayCause,
    expected_custom_message: str | None,
) -> None:
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url=GITHUB_API_URL
    ) as client:
        notifier = GitHubVersionUpdateGateway("owner", "repo", client=client)
        with pytest.raises(VersionUpdateGatewayError) as excinfo:
            await notifier.fetch_update()

    assert excinfo.value.cause == expected_cause
    if expected_custom_message is not None:
        assert str(excinfo.value) == expected_custom_message
