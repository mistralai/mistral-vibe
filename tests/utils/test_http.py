from __future__ import annotations

import json

import httpx
from mistralai.client import Mistral
import pytest

from vibe import __version__
from vibe.utils.http import (
    disable_sdk_env_telemetry,
    get_server_url_from_api_base,
    get_user_agent,
    mistral_sdk_client,
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


@pytest.mark.asyncio
async def test_a_stream_survives_the_sdks_own_tracing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """*Prepare*: MISTRAL_SDK_TELEMETRY set, which turns on the SDK's own tracing,
    and a Mistral client on our httpx client with that tracing disabled.
    *Do*: Stream a chat completion.
    *Assert*: The chunks arrive. With the SDK's tracing on, the SDK wraps our httpx
    response in an httpx2 one, whose close refuses httpx's stream.
    """
    # Prepare
    monkeypatch.setenv("MISTRAL_SDK_TELEMETRY", "dedicated")
    chunk = {
        "id": "1",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}],
    }
    body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()

    def reply(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=httpx.ByteStream(body),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    client = Mistral(
        api_key="key",
        server_url="https://provider.example",
        async_client=mistral_sdk_client(http_client),
    )
    disable_sdk_env_telemetry(client)

    # Do
    stream = await client.chat.stream_async(
        model="m", messages=[{"role": "user", "content": "hello"}]
    )
    contents = [event.data.choices[0].delta.content async for event in stream]

    # Assert
    assert contents == ["hi"]
