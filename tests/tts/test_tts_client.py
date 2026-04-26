from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vibe.core.config import TTSModelConfig, TTSProviderConfig
from vibe.core.tts import MistralTTSClient, TTSResult


def _make_provider() -> TTSProviderConfig:
    return TTSProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai",
        api_key_env_var="MISTRAL_API_KEY",
    )


def _make_model() -> TTSModelConfig:
    return TTSModelConfig(
        name="voxtral-mini-tts-latest",
        alias="voxtral-tts",
        provider="mistral",
        voice="gb_jane_neutral",
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

        client = MistralTTSClient(_make_provider(), _make_model())

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
        )

    @pytest.mark.asyncio
    async def test_speak_raises_on_sdk_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
        import httpx
        from mistralai.client.errors import SDKError

        fake_response = httpx.Response(
            status_code=500,
            request=httpx.Request("POST", "https://api.mistral.ai/v1/audio/speech"),
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
