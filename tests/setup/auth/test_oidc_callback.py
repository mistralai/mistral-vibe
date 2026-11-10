from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncIterator
import hashlib
import re

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


def parse(response: bytes) -> tuple[dict[str, str], str]:
    head, body = response.split(b"\r\n\r\n", 1)
    headers = dict(line.decode().split(": ", 1) for line in head.split(b"\r\n")[1:])
    return headers, body.decode()


def assert_branded_page(response: bytes, title: str, message: str) -> None:
    headers, html = parse(response)
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Cache-Control"] == "no-store"
    assert int(headers["Content-Length"]) == len(html.encode())
    styles = re.findall(r"<style>(.*?)</style>", html, re.DOTALL)
    assert len(styles) == 1
    css_hash = base64.b64encode(hashlib.sha256(styles[0].encode()).digest()).decode()
    assert headers["Content-Security-Policy"] == (
        f"default-src 'none'; style-src 'sha256-{css_hash}'; base-uri 'none'; "
        "form-action 'none'; frame-ancestors 'none'"
    )
    assert "<script" not in html
    assert "style=" not in html
    urls = re.findall(r"https?://[^\s\"'<>)]+", html)
    assert urls == ["http://www.w3.org/2000/svg"]
    assert re.findall(r"<h1>(.*?)</h1>", html) == [title]
    assert re.findall(r"<p>(.*?)</p>", html) == [message]


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
    assert_branded_page(
        response, "Sign-in failed", "Return to your terminal and try again."
    )
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
    assert_branded_page(
        response,
        "Sign-in complete",
        "You can close this window and return to your terminal.",
    )
    assert parse(response)[0]["Connection"] == "close"
    assert b"authorization-secret" not in response
    assert b"expected-state" not in response
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
    assert_branded_page(
        response, "Sign-in was cancelled", "Return to your terminal to try again."
    )
    with pytest.raises(OidcError, match="authorization was declined") as raised:
        await callback.result
    assert "secret" not in str(raised.value)
    assert b"secret" not in response


@pytest.mark.parametrize("error", ["server_error", "invalid_scope"])
@pytest.mark.asyncio
async def test_callback_provider_error_other_than_denial_reports_failure(
    callback, error
) -> None:
    response = await get(callback, f"/callback?state=expected-state&error={error}")
    assert response.startswith(b"HTTP/1.1 200 OK")
    assert_branded_page(
        response, "Sign-in failed", "Return to your terminal and try again."
    )
    with pytest.raises(OidcError, match="authorization was declined"):
        await callback.result


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
