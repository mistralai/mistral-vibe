from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import MagicMock

import pytest

from vibe.core.config import TranscribeModelConfig, TranscribeProviderConfig
from vibe.core.transcribe import (
    MistralTranscribeClient,
    TranscribeDone,
    TranscribeError,
    TranscribeSessionCreated,
    TranscribeTextDelta,
)


def _make_provider() -> TranscribeProviderConfig:
    return TranscribeProviderConfig(
        name="mistral",
        api_base="https://api.mistral.ai",
        api_key_env_var="MISTRAL_API_KEY",
    )


def _make_model() -> TranscribeModelConfig:
    return TranscribeModelConfig(
        name="mistral-small-transcribe",
        alias="foo",
        provider="mistral",
        encoding="pcm_s16le",
        sample_rate=16_000,
        target_streaming_delay_ms=200,
    )


async def _empty_audio_stream() -> AsyncIterator[bytes]:
    return
    yield


def _make_sdk_session_created(request_id: str = "test-request-id") -> MagicMock:
    from mistralai.client.models import (
        RealtimeTranscriptionSession,
        RealtimeTranscriptionSessionCreated,
    )

    session = MagicMock(spec=RealtimeTranscriptionSession)
    session.request_id = request_id
    mock = MagicMock(spec=RealtimeTranscriptionSessionCreated)
    mock.session = session
    return mock


def _make_sdk_text_delta(text: str) -> MagicMock:
    from mistralai.client.models import TranscriptionStreamTextDelta

    m = MagicMock(spec=TranscriptionStreamTextDelta)
    m.text = text
    return m


def _make_sdk_done(text: str) -> MagicMock:
    from mistralai.client.models import TranscriptionStreamDone

    m = MagicMock(spec=TranscriptionStreamDone)
    m.text = text
    return m


def _make_sdk_error(message: str) -> MagicMock:
    from mistralai.client.models import RealtimeTranscriptionError

    m = MagicMock(spec=RealtimeTranscriptionError)
    m.error = MagicMock()
    m.error.message = message
    return m


def _make_sdk_unknown() -> MagicMock:
    from mistralai.extra.realtime import UnknownRealtimeEvent

    return MagicMock(spec=UnknownRealtimeEvent)


async def _collect(client: MistralTranscribeClient) -> list[object]:
    events: list[object] = []
    async for event in client.transcribe(_empty_audio_stream()):
        events.append(event)
    return events


def _patch_sdk(sdk_events: list[object]) -> MagicMock:
    async def _fake_stream(**_kwargs: object) -> AsyncIterator[object]:
        for e in sdk_events:
            yield e

    mock_client = MagicMock()
    mock_client.audio.realtime.transcribe_stream = _fake_stream
    return mock_client


class TestEventMapping:
    @pytest.mark.asyncio
    async def test_session_created(self) -> None:
        mock_client = _patch_sdk([_make_sdk_session_created()])
        client = MistralTranscribeClient(_make_provider(), _make_model())
        client._client = mock_client

        events = await _collect(client)

        assert len(events) == 1
        assert isinstance(events[0], TranscribeSessionCreated)
        assert events[0].request_id == "test-request-id"

    @pytest.mark.asyncio
    async def test_text_delta(self) -> None:
        mock_client = _patch_sdk([_make_sdk_text_delta("hello")])
        client = MistralTranscribeClient(_make_provider(), _make_model())
        client._client = mock_client

        events = await _collect(client)

        assert len(events) == 1
        assert isinstance(events[0], TranscribeTextDelta)
        assert events[0].text == "hello"

    @pytest.mark.asyncio
    async def test_done(self) -> None:
        mock_client = _patch_sdk([_make_sdk_done("full text")])
        client = MistralTranscribeClient(_make_provider(), _make_model())
        client._client = mock_client

        events = await _collect(client)

        assert len(events) == 1
        assert isinstance(events[0], TranscribeDone)

    @pytest.mark.asyncio
    async def test_error(self) -> None:
        mock_client = _patch_sdk([_make_sdk_error("something broke")])
        client = MistralTranscribeClient(_make_provider(), _make_model())
        client._client = mock_client

        events = await _collect(client)

        assert len(events) == 1
        assert isinstance(events[0], TranscribeError)
        assert events[0].message == "something broke"

    @pytest.mark.asyncio
    async def test_unknown_event_is_skipped(self) -> None:
        mock_client = _patch_sdk([
            _make_sdk_session_created(),
            _make_sdk_unknown(),
            _make_sdk_text_delta("hi"),
        ])
        client = MistralTranscribeClient(_make_provider(), _make_model())
        client._client = mock_client

        events = await _collect(client)

        assert len(events) == 2
        assert isinstance(events[0], TranscribeSessionCreated)
        assert isinstance(events[1], TranscribeTextDelta)
