from __future__ import annotations

import asyncio
import hmac
from urllib.parse import parse_qs, urlsplit

from vibe.setup.auth._oidc_callback_page import (
    CALLBACK_PAGE_HEADERS,
    CallbackPage,
    render_callback_page,
)
from vibe.setup.auth._oidc_config import OidcError

_MAX_CONNECTIONS = 8


class OidcCallback:
    """Bounded loopback callback; unrelated requests cannot complete login."""

    def __init__(self, redirect_uri: str, state: str) -> None:
        self.redirect_uri = redirect_uri
        self.state = state
        self.result: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._server: asyncio.Server | None = None
        self._connections: set[asyncio.Task[None]] = set()
        self._closing = False

    async def __aenter__(self) -> OidcCallback:
        parsed = urlsplit(self.redirect_uri)
        self._server = await asyncio.start_server(
            self._handle, parsed.hostname, parsed.port, limit=8192
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        self._closing = True
        if self._server is not None:
            self._server.close()
        for task in self._connections:
            task.cancel()
        await asyncio.gather(*self._connections, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
        if not self.result.done():
            self.result.cancel()

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        if task is None:
            writer.close()
            return
        if self._closing or len(self._connections) >= _MAX_CONNECTIONS:
            writer.close()
            return
        self._connections.add(task)
        status, page = "400 Bad Request", CallbackPage.FAILED
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            line = request.split(b"\r\n", 1)[0].decode("ascii")
            method, target, _ = line.split(" ")
            parsed = urlsplit(target)
            query = parse_qs(parsed.query)
            valid_state = query.get("state", [])
            valid_target = (
                method == "GET"
                and not parsed.scheme
                and not parsed.netloc
                and parsed.path == urlsplit(self.redirect_uri).path
            )
            if (
                valid_target
                and len(valid_state) == 1
                and hmac.compare_digest(valid_state[0].encode(), self.state.encode())
                and not self.result.done()
            ):
                if "error" in query:
                    self.result.set_exception(
                        OidcError("OIDC authorization was declined")
                    )
                    cancelled = query["error"] == ["access_denied"]
                    status = "200 OK"
                    page = CallbackPage.CANCELLED if cancelled else CallbackPage.FAILED
                elif len(query.get("code", [])) == 1:
                    self.result.set_result(f"{self.redirect_uri}?{parsed.query}")
                    status, page = "200 OK", CallbackPage.COMPLETE
            body = render_callback_page(page)
            headers = "".join(
                f"{name}: {value}\r\n" for name, value in CALLBACK_PAGE_HEADERS
            )
            writer.write(
                f"HTTP/1.1 {status}\r\n{headers}"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await asyncio.wait_for(writer.drain(), 5)
        except (
            TimeoutError,
            ValueError,
            OSError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass
        finally:
            writer.close()
            self._connections.discard(task)
