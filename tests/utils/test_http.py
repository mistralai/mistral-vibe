from __future__ import annotations

import json

import httpx2
from mistralai.client import Mistral
import pytest

from vibe import __version__
from vibe.utils.http import (
    VibeAsyncHTTPClient,
    get_server_url_from_api_base,
    get_user_agent,
    with_user_agent,
)


@pytest.mark.parametrize(
    ("api_base", "expected"),
    [
        ("https://api.mistral.ai/v1", "https://api.mistral.ai"),
        ("https://on-prem.example.com/v1", "https://on-prem.example.com"),
        ("http://localhost:8080/v2", "http://localhost:8080"),
        ("not-a-url", None),
        ("ftp://example.com/v1", None),
    ],
)
def test_get_server_url_from_api_base(api_base: str, expected: str | None) -> None:
    assert get_server_url_from_api_base(api_base) == expected


@pytest.mark.parametrize(
    ("backend", "expected"),
    [
        ("mistral", f"mistral-client-python/Mistral-Vibe/{__version__}"),
        ("generic", f"Mistral-Vibe/{__version__}"),
        (None, f"Mistral-Vibe/{__version__}"),
    ],
)
def test_get_user_agent(backend: str | None, expected: str) -> None:
    assert get_user_agent(backend) == expected


def test_with_user_agent_adds_vibes_user_agent() -> None:
    assert with_user_agent({"X-Team": "a"}, "generic") == {
        "X-Team": "a",
        "user-agent": f"Mistral-Vibe/{__version__}",
    }


def test_with_user_agent_keeps_the_users_own_user_agent() -> None:
    assert with_user_agent({"User-Agent": "custom"}, "generic") == {
        "User-Agent": "custom"
    }


@pytest.mark.asyncio
async def test_a_stream_survives_the_sdks_own_tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: MISTRAL_SDK_TELEMETRY set, which turns on the SDK's own tracing,
    and a Mistral client on the Vibe HTTP client.
    *Do*: Stream a chat completion.
    *Assert*: The chunks arrive. The tracing wraps each response in an HTTPX2
    one, whose close refuses a stream that is not HTTPX2's.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_SDK_TELEMETRY", "dedicated")
    # The spans go to a closed local port, and their export gives up at once.
    monkeypatch.setenv("MISTRAL_OTLP_TRACES_ENDPOINT", "http://127.0.0.1:9/v1/traces")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_TIMEOUT", "0.1")
    chunk = {
        "id": "1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
    }
    body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()

    def reply(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=httpx2.ByteStream(body),
        )

    http_client = VibeAsyncHTTPClient(transport=httpx2.MockTransport(reply))
    client = Mistral(
        api_key="key", server_url="https://provider.example", async_client=http_client
    )

    # Do
    stream = await client.chat.stream_async(
        model="m", messages=[{"role": "user", "content": "hello"}]
    )
    contents = [event.data.choices[0].delta.content async for event in stream]

    # Assert
    assert contents == ["hi"]


@pytest.mark.asyncio
async def test_the_sdk_client_resolves_the_sdks_default_timeout() -> None:
    """*Prepare*: The client Vibe gives the Mistral SDK, with a 5-second timeout.
    *Do*: Build a request the way the SDK does without ``timeout_ms``.
    *Assert*: The request carries the client's timeout, not the SDK's HTTPX2
    ``USE_CLIENT_DEFAULT`` sentinel, which an HTTPX client would store as is.
    """
    # Prepare
    async with VibeAsyncHTTPClient(timeout=5.0) as client:
        # Do
        request = client.build_request(
            "GET", "https://provider.example", timeout=httpx2.USE_CLIENT_DEFAULT
        )

    # Assert
    assert request.extensions["timeout"] == {
        "connect": 5.0,
        "read": 5.0,
        "write": 5.0,
        "pool": 5.0,
    }
