from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from vibe.core.auth.github import (
    DeviceFlowHandle,
    DeviceFlowInfo,
    GitHubAuthError,
    GitHubAuthProvider,
)


class TestDeviceFlowModels:
    def test_device_flow_info(self) -> None:
        info = DeviceFlowInfo(
            user_code="ABC-123", verification_uri="https://example.com"
        )
        assert info.user_code == "ABC-123"
        assert info.verification_uri == "https://example.com"

    def test_device_flow_handle(self) -> None:
        info = DeviceFlowInfo(
            user_code="ABC-123", verification_uri="https://example.com"
        )
        handle = DeviceFlowHandle(device_code="dc_123", expires_in=900, info=info)
        assert handle.device_code == "dc_123"
        assert handle.expires_in == 900
        assert handle.info.user_code == "ABC-123"


class TestGitHubAuthProviderContextManager:
    @pytest.mark.asyncio
    async def test_creates_client_on_enter(self) -> None:
        provider = GitHubAuthProvider()
        assert provider._client is None
        async with provider:
            assert provider._client is not None
        assert provider._client is None

    @pytest.mark.asyncio
    async def test_uses_provided_client(self) -> None:
        external_client = httpx.AsyncClient()
        provider = GitHubAuthProvider(client=external_client)
        async with provider:
            assert provider._client is external_client
        assert provider._client is external_client
        await external_client.aclose()


class TestGitHubAuthProviderGetToken:
    def test_returns_token_from_keyring(self) -> None:
        with patch("vibe.core.auth.github.keyring") as mock_keyring:
            mock_keyring.get_password.return_value = "ghp_test_token"
            provider = GitHubAuthProvider()
            token = provider.get_token()
            assert token == "ghp_test_token"

    def test_returns_none_on_keyring_error(self) -> None:
        with patch("vibe.core.auth.github.keyring") as mock_keyring:
            import keyring.errors

            mock_keyring.errors = keyring.errors
            mock_keyring.get_password.side_effect = keyring.errors.KeyringError("error")
            provider = GitHubAuthProvider()
            token = provider.get_token()
            assert token is None

    def test_returns_none_when_no_token(self) -> None:
        with patch("vibe.core.auth.github.keyring") as mock_keyring:
            mock_keyring.get_password.return_value = None
            provider = GitHubAuthProvider()
            token = provider.get_token()
            assert token is None


class TestGitHubAuthProviderHasToken:
    def test_returns_true_when_token_exists(self) -> None:
        with patch("vibe.core.auth.github.keyring") as mock_keyring:
            mock_keyring.get_password.return_value = "ghp_token"
            provider = GitHubAuthProvider()
            assert provider.has_token() is True

    def test_returns_false_when_no_token(self) -> None:
        with patch("vibe.core.auth.github.keyring") as mock_keyring:
            mock_keyring.get_password.return_value = None
            provider = GitHubAuthProvider()
            assert provider.has_token() is False


class TestGitHubAuthProviderStartDeviceFlow:
    @pytest.fixture
    def mock_client(self) -> MagicMock:
        return MagicMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def provider(self, mock_client: MagicMock) -> GitHubAuthProvider:
        return GitHubAuthProvider(client=mock_client)

    @pytest.mark.asyncio
    async def test_start_device_flow_success(
        self, provider: GitHubAuthProvider, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "device_code": "dc_123",
            "user_code": "ABC-123",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("vibe.core.auth.github.webbrowser") as mock_browser:
            handle = await provider.start_device_flow(open_browser=True)
            mock_browser.open.assert_called_once_with("https://github.com/login/device")

        assert handle.device_code == "dc_123"
        assert handle.info.user_code == "ABC-123"
        assert handle.expires_in == 900

    @pytest.mark.asyncio
    async def test_start_device_flow_without_browser(
        self, provider: GitHubAuthProvider, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = True
        mock_response.json.return_value = {
            "device_code": "dc_123",
            "user_code": "ABC-123",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 900,
        }
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("vibe.core.auth.github.webbrowser") as mock_browser:
            await provider.start_device_flow(open_browser=False)
            mock_browser.open.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_device_flow_failure(
        self, provider: GitHubAuthProvider, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.is_success = False
        mock_response.text = "Bad request"
        mock_client.post = AsyncMock(return_value=mock_response)

        with pytest.raises(GitHubAuthError, match="Failed to initiate device flow"):
            await provider.start_device_flow()


class TestGitHubAuthProviderPollForToken:
    @pytest.fixture
    def mock_client(self) -> MagicMock:
        return MagicMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def provider(self, mock_client: MagicMock) -> GitHubAuthProvider:
        return GitHubAuthProvider(client=mock_client)

    @pytest.mark.asyncio
    async def test_poll_returns_token_on_success(
        self, provider: GitHubAuthProvider, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "ghp_new_token"}
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("vibe.core.auth.github.asyncio.sleep", new_callable=AsyncMock):
            token = await provider._poll_for_token(
                mock_client, "dc_123", expires_in=10, interval=1
            )
        assert token == "ghp_new_token"

    @pytest.mark.asyncio
    async def test_poll_handles_slow_down(
        self, provider: GitHubAuthProvider, mock_client: MagicMock
    ) -> None:
        responses = [
            MagicMock(
                json=MagicMock(return_value={"error": "slow_down", "interval": 5})
            ),
            MagicMock(json=MagicMock(return_value={"access_token": "ghp_token"})),
        ]
        mock_client.post = AsyncMock(side_effect=responses)

        with patch("vibe.core.auth.github.asyncio.sleep", new_callable=AsyncMock):
            token = await provider._poll_for_token(
                mock_client, "dc_123", expires_in=30, interval=1
            )
        assert token == "ghp_token"

    @pytest.mark.asyncio
    async def test_poll_raises_on_expired_token(
        self, provider: GitHubAuthProvider, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "expired_token"}
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("vibe.core.auth.github.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(GitHubAuthError, match="expired_token"):
                await provider._poll_for_token(
                    mock_client, "dc_123", expires_in=10, interval=1
                )

    @pytest.mark.asyncio
    async def test_poll_raises_on_access_denied(
        self, provider: GitHubAuthProvider, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "access_denied"}
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("vibe.core.auth.github.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(GitHubAuthError, match="access_denied"):
                await provider._poll_for_token(
                    mock_client, "dc_123", expires_in=10, interval=1
                )

    @pytest.mark.asyncio
    async def test_poll_raises_on_timeout(
        self, provider: GitHubAuthProvider, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "authorization_pending"}
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("vibe.core.auth.github.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(GitHubAuthError, match="timed out"):
                await provider._poll_for_token(
                    mock_client, "dc_123", expires_in=2, interval=1
                )


class TestGitHubAuthProviderSaveToken:
    def test_save_token_success(self) -> None:
        with patch("vibe.core.auth.github.keyring") as mock_keyring:
            provider = GitHubAuthProvider()
            provider._save_token("ghp_token")
            mock_keyring.set_password.assert_called_once_with(
                "vibe", "github_token", "ghp_token"
            )

    def test_save_token_raises_on_keyring_error(self) -> None:
        with patch("vibe.core.auth.github.keyring") as mock_keyring:
            import keyring.errors

            mock_keyring.errors = keyring.errors
            mock_keyring.set_password.side_effect = keyring.errors.KeyringError("error")
            provider = GitHubAuthProvider()
            with pytest.raises(GitHubAuthError, match="Failed to save token"):
                provider._save_token("ghp_token")


class TestGitHubAuthProviderWaitForToken:
    @pytest.fixture
    def mock_client(self) -> MagicMock:
        return MagicMock(spec=httpx.AsyncClient)

    @pytest.fixture
    def provider(self, mock_client: MagicMock) -> GitHubAuthProvider:
        return GitHubAuthProvider(client=mock_client)

    @pytest.mark.asyncio
    async def test_wait_for_token_polls_and_saves(
        self, provider: GitHubAuthProvider, mock_client: MagicMock
    ) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "ghp_token"}
        mock_client.post = AsyncMock(return_value=mock_response)

        info = DeviceFlowInfo(user_code="ABC", verification_uri="https://example.com")
        handle = DeviceFlowHandle(device_code="dc_123", expires_in=10, info=info)

        with (
            patch("vibe.core.auth.github.asyncio.sleep", new_callable=AsyncMock),
            patch("vibe.core.auth.github.keyring") as mock_keyring,
        ):
            token = await provider.wait_for_token(handle)

        assert token == "ghp_token"
        mock_keyring.set_password.assert_called_once()
