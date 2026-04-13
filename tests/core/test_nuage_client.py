from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from vibe.core.teleport.errors import ServiceTeleportError
from vibe.core.teleport.nuage import GitHubPublicData, GitHubStatus, NuageClient


@pytest.fixture
def client() -> NuageClient:
    return NuageClient(
        base_url="https://test.example.com", api_key="test-key", workflow_id="wf-1"
    )


class TestWaitForGithubConnection:
    @pytest.mark.asyncio
    async def test_returns_immediately_when_connected(
        self, client: NuageClient
    ) -> None:
        connected = GitHubPublicData(status=GitHubStatus.CONNECTED)
        client.get_github_integration = AsyncMock(return_value=connected)

        result = await client.wait_for_github_connection("exec-1")

        assert result.connected is True
        client.get_github_integration.assert_called_once_with("exec-1")

    @pytest.mark.asyncio
    async def test_polls_until_connected(self, client: NuageClient) -> None:
        pending = GitHubPublicData(status=GitHubStatus.PENDING)
        waiting = GitHubPublicData(
            status=GitHubStatus.WAITING_FOR_OAUTH, oauth_url="https://github.com/auth"
        )
        connected = GitHubPublicData(status=GitHubStatus.CONNECTED)
        client.get_github_integration = AsyncMock(
            side_effect=[pending, waiting, connected]
        )

        with patch("vibe.core.teleport.nuage.asyncio.sleep", new_callable=AsyncMock):
            result = await client.wait_for_github_connection("exec-1")

        assert result.connected is True
        assert client.get_github_integration.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_on_error_status(self, client: NuageClient) -> None:
        error_data = GitHubPublicData(
            status=GitHubStatus.ERROR, error="App not installed"
        )
        client.get_github_integration = AsyncMock(return_value=error_data)

        with pytest.raises(ServiceTeleportError, match="App not installed"):
            await client.wait_for_github_connection("exec-1")

    @pytest.mark.asyncio
    async def test_raises_on_oauth_timeout(self, client: NuageClient) -> None:
        timeout_data = GitHubPublicData(status=GitHubStatus.OAUTH_TIMEOUT)
        client.get_github_integration = AsyncMock(return_value=timeout_data)

        with pytest.raises(ServiceTeleportError, match="oauth_timeout"):
            await client.wait_for_github_connection("exec-1")

    @pytest.mark.asyncio
    async def test_raises_on_timeout(self, client: NuageClient) -> None:
        pending = GitHubPublicData(status=GitHubStatus.PENDING)
        client.get_github_integration = AsyncMock(return_value=pending)

        monotonic_values = iter([0.0, 0.0, 601.0])
        with (
            patch(
                "vibe.core.teleport.nuage.time.monotonic",
                side_effect=lambda: next(monotonic_values),
            ),
            patch("vibe.core.teleport.nuage.asyncio.sleep", new_callable=AsyncMock),
            pytest.raises(ServiceTeleportError, match="timed out"),
        ):
            await client.wait_for_github_connection("exec-1", timeout=600.0)

    @pytest.mark.asyncio
    async def test_sleeps_with_correct_interval(self, client: NuageClient) -> None:
        pending = GitHubPublicData(status=GitHubStatus.PENDING)
        connected = GitHubPublicData(status=GitHubStatus.CONNECTED)
        client.get_github_integration = AsyncMock(side_effect=[pending, connected])

        with patch(
            "vibe.core.teleport.nuage.asyncio.sleep", new_callable=AsyncMock
        ) as mock_sleep:
            await client.wait_for_github_connection("exec-1", interval=5.0)

        mock_sleep.assert_called_once()
        sleep_duration = mock_sleep.call_args[0][0]
        assert sleep_duration <= 5.0
