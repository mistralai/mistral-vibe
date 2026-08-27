from __future__ import annotations

from collections.abc import AsyncIterator
import json
from unittest.mock import MagicMock

import pytest

from vibe.app_server.config import AudioProviderView, TranscribeModelConfigView
from vibe.cli.transcribe import (
    MistralTranscribeClient,
    TranscribeDone,
    TranscribeError,
    TranscribeSessionCreated,
    TranscribeTextDelta,
)


def _make_provider() -> AudioProviderView:
    return AudioProviderView(
        api_base="https://api.mistral.ai",
        api_key_env_var="MISTRAL_API_KEY",
        client="mistral",
    )


def _make_model() -> TranscribeModelConfigView:
    return TranscribeModelConfigView(
        name="mistral-small-transcribe",
        encoding="pcm_s16le",
        sample_rate=16_000,
        language="en",
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
    async def test_empty_recording_error_is_treated_as_done(self) -> None:
        mock_client = _patch_sdk([
            _make_sdk_error("Cannot flush audio before sending any audio bytes")
        ])
        client = MistralTranscribeClient(_make_provider(), _make_model())
        client._client = mock_client

        events = await _collect(client)

        assert len(events) == 1
        assert isinstance(events[0], TranscribeDone)

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


def _patch_sdk_then_raise(sdk_events: list[object], exc: Exception) -> MagicMock:
    async def _fake_stream(**kwargs: object) -> AsyncIterator[object]:
        audio_stream = kwargs["audio_stream"]
        assert isinstance(audio_stream, AsyncIterator)
        async for _ in audio_stream:
            pass
        for e in sdk_events:
            yield e
        raise exc

    mock_client = MagicMock()
    mock_client.audio.realtime.transcribe_stream = _fake_stream
    return mock_client


def _patch_sdk_drop_midstream(sdk_events: list[object], exc: Exception) -> MagicMock:
    async def _fake_stream(**kwargs: object) -> AsyncIterator[object]:
        audio_stream = kwargs["audio_stream"]
        assert isinstance(audio_stream, AsyncIterator)
        await audio_stream.__anext__()
        for e in sdk_events:
            yield e
        raise exc

    mock_client = MagicMock()
    mock_client.audio.realtime.transcribe_stream = _fake_stream
    return mock_client


async def _pending_audio_stream() -> AsyncIterator[bytes]:
    yield b"\x00\x00"
    yield b"\x00\x00"


async def _collect_from(
    client: MistralTranscribeClient, audio_stream: AsyncIterator[bytes]
) -> list[object]:
    events: list[object] = []
    async for event in client.transcribe(audio_stream):
        events.append(event)
    return events


class TestRequestMetadata:
    @pytest.mark.asyncio
    async def test_serializes_metadata_in_request_header(self) -> None:
        captured_headers: dict[str, str] = {}

        async def _fake_stream(**kwargs: object) -> AsyncIterator[object]:
            headers = kwargs["http_headers"]
            assert isinstance(headers, dict)
            captured_headers.update(headers)
            yield _make_sdk_done("")

        mock_client = MagicMock()
        mock_client.audio.realtime.transcribe_stream = _fake_stream
        client = MistralTranscribeClient(
            _make_provider(),
            _make_model(),
            metadata_getter=lambda: {
                "session_id": "session-1",
                "call_type": "secondary_call",
            },
        )
        client._client = mock_client

        await _collect(client)

        assert json.loads(captured_headers["x-metadata"]) == {
            "session_id": "session-1",
            "call_type": "secondary_call",
        }


class TestConnectionCloseRace:
    @pytest.mark.asyncio
    async def test_connection_closed_after_output_is_swallowed(self) -> None:
        mock_client = _patch_sdk_then_raise(
            [_make_sdk_text_delta("hi"), _make_sdk_done("hi")],
            RuntimeError("Connection is closed"),
        )
        client = MistralTranscribeClient(_make_provider(), _make_model())
        client._client = mock_client

        events = await _collect(client)

        assert [type(e) for e in events] == [TranscribeTextDelta, TranscribeDone]

    @pytest.mark.asyncio
    async def test_connection_closed_without_text_completes_benignly(self) -> None:
        mock_client = _patch_sdk_then_raise(
            [_make_sdk_session_created()], RuntimeError("Connection is closed")
        )
        client = MistralTranscribeClient(_make_provider(), _make_model())
        client._client = mock_client

        events = await _collect(client)

        assert [type(e) for e in events] == [TranscribeSessionCreated, TranscribeDone]

    @pytest.mark.asyncio
    async def test_connection_closed_after_text_without_done_completes(self) -> None:
        mock_client = _patch_sdk_then_raise(
            [_make_sdk_text_delta("hi")], RuntimeError("Connection is closed")
        )
        client = MistralTranscribeClient(_make_provider(), _make_model())
        client._client = mock_client

        events = await _collect(client)

        assert [type(e) for e in events] == [TranscribeTextDelta, TranscribeDone]

    @pytest.mark.asyncio
    async def test_other_runtime_error_propagates(self) -> None:
        mock_client = _patch_sdk_then_raise([], RuntimeError("boom"))
        client = MistralTranscribeClient(_make_provider(), _make_model())
        client._client = mock_client

        with pytest.raises(RuntimeError, match="boom"):
            await _collect(client)

    @pytest.mark.asyncio
    async def test_connection_closed_midstream_surfaces_error(self) -> None:
        mock_client = _patch_sdk_drop_midstream(
            [_make_sdk_session_created(), _make_sdk_text_delta("hi")],
            RuntimeError("Connection is closed"),
        )
        client = MistralTranscribeClient(_make_provider(), _make_model())
        client._client = mock_client

        events = await _collect_from(client, _pending_audio_stream())

        assert [type(e) for e in events] == [
            TranscribeSessionCreated,
            TranscribeTextDelta,
            TranscribeError,
        ]
        assert isinstance(events[-1], TranscribeError)
