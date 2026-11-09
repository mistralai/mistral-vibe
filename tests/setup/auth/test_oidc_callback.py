from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from vibe.setup.auth._oidc_callback import OidcCallback
from vibe.setup.auth._oidc_config import OidcError


@pytest_asyncio.fixture
async def callback() -> AsyncIterator[OidcCallback]:
    async with OidcCallback(
        "http://127.0.0.1:0/callback", "expected-state"
    ) as listener:
        yield listener


async def connect(
    callback: OidcCallback,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    assert callback._server is not None
    sockets = callback._server.sockets
    assert len(sockets) == 1
    host, port = sockets[0].getsockname()[:2]
    assert host == "127.0.0.1"
    return await asyncio.wait_for(asyncio.open_connection(host, port), 1)


async def send(callback: OidcCallback, request: bytes) -> bytes:
    reader, writer = await connect(callback)
    try:
        writer.write(request)
        await writer.drain()
        return await asyncio.wait_for(reader.read(), 1)
    finally:
        writer.close()
        await writer.wait_closed()


async def get(callback: OidcCallback, target: str, method: str = "GET") -> bytes:
    return await send(
        callback, f"{method} {target} HTTP/1.1\r\nHost: localhost\r\n\r\n".encode()
    )


@pytest.mark.parametrize(
    "target,method",
    [
        ("/favicon.ico?state=expected-state&code=code-secret", "GET"),
        ("/callback?state=wrong&code=code-secret", "GET"),
        ("/callback?state=%C3%A9&code=code-secret", "GET"),
        ("/callback?code=code-secret", "GET"),
        ("/callback?state=expected-state&state=wrong&code=code-secret", "GET"),
        ("/callback?state=expected-state&code=first&code=second", "GET"),
        ("/callback?state=expected-state", "GET"),
        ("/callback?state=expected-state&code=", "GET"),
        ("/callback?state=expected-state&code=code-secret", "POST"),
        ("http://other.example/callback?state=expected-state&code=code-secret", "GET"),
        ("//other.example/callback?state=expected-state&code=code-secret", "GET"),
    ],
)
@pytest.mark.asyncio
async def test_unrelated_or_ambiguous_request_cannot_complete_login(
    callback, target, method
) -> None:
    response = await get(callback, target, method)
    assert response.startswith(b"HTTP/1.1 400 Bad Request")
    assert not callback.result.done()
    assert b"code-secret" not in response
    valid = await get(callback, "/callback?state=expected-state&code=valid-code")
    assert valid.startswith(b"HTTP/1.1 200 OK")
    assert (
        await callback.result
        == f"{callback.redirect_uri}?state=expected-state&code=valid-code"
    )


@pytest.mark.asyncio
async def test_callback_success_is_single_use_and_does_not_echo_secrets(
    callback,
) -> None:
    target = "/callback?state=expected-state&code=authorization-secret"
    response = await get(callback, target)
    assert response.startswith(b"HTTP/1.1 200 OK")
    assert b"Cache-Control: no-store" in response
    assert b"Connection: close" in response
    assert b"authorization-secret" not in response
    assert b"expected-state" not in response
    headers, body = response.split(b"\r\n\r\n", 1)
    content_length = next(
        line for line in headers.split(b"\r\n") if line.startswith(b"Content-Length:")
    )
    assert int(content_length.split(b":", 1)[1]) == len(body)
    initial = callback.result.result()
    replay = await get(callback, "/callback?state=expected-state&code=second-secret")
    assert replay.startswith(b"HTTP/1.1 400")
    assert callback.result.result() == initial


@pytest.mark.asyncio
async def test_callback_decline_is_safe_and_requires_matching_state(callback) -> None:
    response = await get(callback, "/callback?state=wrong&error=access_denied")
    assert response.startswith(b"HTTP/1.1 400")
    assert not callback.result.done()
    response = await get(
        callback,
        "/callback?state=expected-state&error=access_denied&error_description=secret",
    )
    assert response.startswith(b"HTTP/1.1 200 OK")
    assert response.endswith(b"Sign-in declined. Return to Vibe.")
    assert b"Invalid sign-in callback" not in response
    with pytest.raises(OidcError, match="authorization was declined") as raised:
        await callback.result
    assert "secret" not in str(raised.value)
    assert b"secret" not in response


@pytest.mark.asyncio
async def test_callback_shutdown_cancels_pending_result_and_idle_connections() -> None:
    callback = OidcCallback("http://127.0.0.1:0/callback", "expected-state")
    await callback.__aenter__()
    reader, writer = await connect(callback)
    try:
        async with asyncio.timeout(1):
            while len(callback._connections) != 1:
                await asyncio.sleep(0)
        await asyncio.wait_for(callback.__aexit__(), 0.5)
        assert callback.result.cancelled()
        assert callback._server is not None
        assert not callback._server.is_serving()
        assert await asyncio.wait_for(reader.read(), 1) == b""
        assert callback._connections == set()
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_callback_bounds_active_connections_and_releases_them(callback) -> None:
    connections = []
    try:
        for _ in range(8):
            connections.append(await connect(callback))
        async with asyncio.timeout(1):
            while len(callback._connections) != 8:
                await asyncio.sleep(0)
        reader, writer = await connect(callback)
        connections.append((reader, writer))
        assert await asyncio.wait_for(reader.read(), 1) == b""
        assert len(callback._connections) == 8
        assert not callback.result.done()
    finally:
        for _, writer in connections:
            writer.close()
        await asyncio.gather(*(writer.wait_closed() for _, writer in connections))


@pytest.mark.asyncio
async def test_callback_oversized_headers_close_without_unhandled_exception(
    callback, monkeypatch
) -> None:
    errors = []
    loop = asyncio.get_running_loop()
    old_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))
    try:
        response = await send(
            callback, b"GET /callback HTTP/1.1\r\nX-Fill: " + b"x" * 9000 + b"\r\n\r\n"
        )
        assert response == b""
        await asyncio.sleep(0)
        assert errors == []
        assert not callback.result.done()
    finally:
        loop.set_exception_handler(old_handler)
