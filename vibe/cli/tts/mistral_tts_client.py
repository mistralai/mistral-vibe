from __future__ import annotations

import base64

from mistralai.client import Mistral
from mistralai.client.models import SpeechOutputFormat

from vibe.app_server.config import AudioProviderView, TTSModelConfigView
from vibe.cli.audio_request_metadata import RequestMetadataGetter
from vibe.cli.tts.tts_client_port import TTSResult
from vibe.core.platform_auth import (
    PlatformCredentialProvider,
    PlatformCredentialRequired,
    PlatformCredentialSnapshot,
    PlatformCredentialUnavailable,
)
from vibe.core.platform_auth._mistral import resolve_snapshot
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context, get_user_agent


class MistralTTSClient:
    def __init__(
        self,
        provider: AudioProviderView,
        model: TTSModelConfigView,
        metadata_getter: RequestMetadataGetter | None = None,
        credentials: PlatformCredentialProvider | None = None,
    ) -> None:
        if credentials is None:
            from vibe.app_server._credential_selection import cli_process_credentials

            credentials = cli_process_credentials(provider.api_key_env_var)
        self._credentials = credentials
        self._server_url = provider.api_base
        self._model_name = model.name
        self._voice = model.voice
        self._response_format: SpeechOutputFormat = model.response_format
        self._metadata_getter = metadata_getter or dict
        self._client: Mistral | None = None
        self._http_client: VibeAsyncHTTPClient | None = None

    def _get_client(
        self, snapshot: PlatformCredentialSnapshot | None = None
    ) -> Mistral:
        """The SDK client; ``snapshot`` sets the redirect rule of the request
        about to carry it: an API key follows redirects, a user token never.
        """
        if self._client is None:
            # The credential travels per request (``http_headers``).
            self._http_client = VibeAsyncHTTPClient(verify=build_ssl_context())
            self._client = Mistral(
                api_key="", server_url=self._server_url, async_client=self._http_client
            )
        if snapshot is not None and self._http_client is not None:
            self._http_client.follow_redirects = snapshot.follows_redirects
        return self._client

    async def speak(self, text: str) -> TTSResult:
        metadata = self._metadata_getter()
        snapshot = await resolve_snapshot(self._credentials, self._server_url)
        if isinstance(snapshot, PlatformCredentialRequired):
            raise PlatformCredentialUnavailable(snapshot)
        client = self._get_client(snapshot)
        response = await client.audio.speech.complete_async(
            model=self._model_name,
            input=text,
            voice_id=self._voice,
            response_format=self._response_format,
            metadata=metadata,
            http_headers={"user-agent": get_user_agent("mistral"), **snapshot.headers},
        )
        audio_bytes = base64.b64decode(response.audio_data)
        return TTSResult(audio_data=audio_bytes)

    async def close(self) -> None:
        client = self._client
        http_client = self._http_client
        self._client = None
        self._http_client = None
        try:
            if client is not None:
                await client.__aexit__(exc_type=None, exc_val=None, exc_tb=None)
        finally:
            if http_client is not None:
                await http_client.aclose()
