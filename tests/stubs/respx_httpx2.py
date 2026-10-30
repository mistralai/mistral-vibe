"""Let respx answer Vibe's HTTPX2 clients.

respx models its routes on HTTPX and patches HTTPX's connection pools. Vibe's
clients run on HTTPX2, so its pools are patched the same way; the route's
request and response cross over unchanged, as both libraries speak the same
transport interface underneath. A transport error a route raises is HTTPX's,
which an HTTPX2 client would never raise: it is re-raised as HTTPCore2's error
of the same name, which HTTPX2 then maps to its own, as for a real failure.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpcore2
import httpx
from respx.mocks import HTTPCoreMocker


class HTTPCore2Mocker(HTTPCoreMocker):
    name: ClassVar[str] = "httpcore2"
    targets: ClassVar[list[str]] = [
        "httpcore2._async.connection.AsyncHTTPConnection",
        "httpcore2._async.connection_pool.AsyncConnectionPool",
        "httpcore2._async.http_proxy.AsyncHTTPProxy",
    ]

    @classmethod
    async def async_handler(cls, httpx_request: httpx.Request) -> Any:
        try:
            return await super().async_handler(httpx_request)
        except httpx.TransportError as error:
            raise _as_httpcore2(error) from error

    @classmethod
    def from_sync_httpx_response(  # pyright: ignore[reportIncompatibleMethodOverride]
        cls, httpx_response: httpx.Response, target: Any, **kwargs: Any
    ) -> httpcore2.Response:
        return httpcore2.Response(
            status=httpx_response.status_code,
            headers=httpx_response.headers.raw,
            content=httpx_response.stream,
            extensions=httpx_response.extensions,
        )


def _as_httpcore2(error: httpx.TransportError) -> Exception:
    error_type = getattr(httpcore2, type(error).__name__, None)
    if isinstance(error_type, type) and issubclass(error_type, Exception):
        return error_type(str(error))
    return error


__all__ = ["HTTPCore2Mocker"]
