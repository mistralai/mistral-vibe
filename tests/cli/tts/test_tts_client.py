from __future__ import annotations

import base64
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest
import respx

from vibe.app_server.config import AudioProviderView, TTSModelConfigView
from vibe.cli.tts import MistralTTSClient, TTSResult
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialProvider,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)


def _make_provider() -> AudioProviderView:
    return AudioProviderView(
        api_base="https://api.mistral.ai",
        api_key_env_var="MISTRAL_API_KEY",
        client="mistral",
    )


def _make_model() -> TTSModelConfigView:
    return TTSModelConfigView(
        name="voxtral-mini-tts-latest", voice="gb_jane_neutral", response_format="wav"
    )


class TestMistralTTSClientInit:
    def test_lazy_client_creation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        client = MistralTTSClient(_make_provider(), _make_model())
        assert client._client is None

    def test_get_client_creates_mistral_instance(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        client = MistralTTSClient(_make_provider(), _make_model())
        sdk_client = client._get_client()
        assert sdk_client is not None
        assert client._client is sdk_client
        assert client._get_client() is sdk_client


class TestMistralTTSClient:
    @pytest.mark.asyncio
    async def test_speak_returns_decoded_audio(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

        raw_audio = b"fake-audio-data-for-testing"
        encoded_audio = base64.b64encode(raw_audio).decode()

        mock_response = MagicMock()
        mock_response.audio_data = encoded_audio

        mock_complete_async = AsyncMock(return_value=mock_response)

        client = MistralTTSClient(
            _make_provider(),
            _make_model(),
            metadata_getter=lambda: {
                "call_type": "secondary_call",
                "call_source": "vibe_code",
            },
        )

        with patch.object(
            type(client._get_client().audio.speech),
            "complete_async",
            mock_complete_async,
        ):
            result = await client.speak("Hello")

        assert isinstance(result, TTSResult)
        assert result.audio_data == raw_audio
        mock_complete_async.assert_called_once_with(
            model="voxtral-mini-tts-latest",
            input="Hello",
            voice_id="gb_jane_neutral",
            response_format="wav",
            metadata=ANY,
            http_headers=ANY,
        )
        kwargs = mock_complete_async.call_args.kwargs
        assert kwargs["metadata"]["call_type"] == "secondary_call"
        assert kwargs["metadata"]["call_source"] == "vibe_code"
        assert kwargs["http_headers"]["user-agent"].startswith(
            "mistral-client-python/Mistral-Vibe/"
        )

    @pytest.mark.asyncio
    async def test_speak_raises_on_sdk_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        import httpx2
        from mistralai.client.errors import SDKError

        fake_response = httpx2.Response(
            status_code=500,
            request=httpx2.Request("POST", "https://api.mistral.ai/v1/audio/speech"),
        )
        mock_complete_async = AsyncMock(
            side_effect=SDKError("API error", fake_response)
        )

        client = MistralTTSClient(_make_provider(), _make_model())

        with (
            patch.object(
                type(client._get_client().audio.speech),
                "complete_async",
                mock_complete_async,
            ),
            pytest.raises(SDKError),
        ):
            await client.speak("Hello")

    @pytest.mark.asyncio
    async def test_close_resets_client(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        client = MistralTTSClient(_make_provider(), _make_model())
        client._get_client()
        assert client._client is not None
        await client.close()
        assert client._client is None


class _UserToken:
    """A signed-in user's ID token, as Desktop or the CLI session resolves it."""

    async def resolve(self, target: PlatformCredentialTarget) -> object:
        del target
        return PlatformCredentialSnapshot(
            kind="oidc_id_token",
            headers={"Authorization": "Bearer user-id-token"},
            cache_namespace="oidc_id_token:test",
            revision="revision",
        )

    async def reject(self, target: object, **_: object) -> object:
        raise AssertionError("no request was refused")


class TestRedirects:
    @pytest.mark.parametrize(
        ("credentials", "follows"),
        [
            (ApiKeyPlatformCredentialProvider.from_api_key("test-key"), True),
            (_UserToken(), False),
        ],
        ids=["api_key", "user_token"],
    )
    @pytest.mark.asyncio
    @respx.mock
    async def test_only_an_api_key_follows_a_redirect(
        self, credentials: PlatformCredentialProvider, follows: bool
    ) -> None:
        """*Prepare*: A speech endpoint that redirects to another host.
        *Do*: Speak with an API key, then with a user token.
        *Assert*: The API key follows the redirect, as before; the user token
          never reaches the other host.
        """
        # Prepare
        respx.post("https://api.mistral.ai/v1/audio/speech").respond(
            307, headers={"location": "https://elsewhere.example/v1/audio/speech"}
        )
        elsewhere = respx.post("https://elsewhere.example/v1/audio/speech").respond(
            json={"audio_data": base64.b64encode(b"audio").decode()}
        )
        client = MistralTTSClient(
            _make_provider(), _make_model(), credentials=credentials
        )

        # Do
        try:
            result = await client.speak("Hello")
        except Exception:
            result = None
        finally:
            await client.close()

        # Assert
        assert elsewhere.called is follows
        assert (result == TTSResult(audio_data=b"audio")) is follows
