from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import httpx2
from opentelemetry.exporter.http.transport._base import (
    BaseHTTPResult,
    BaseHTTPTransport,
)

from vibe.core.utils.concurrency import run_sync
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context


@dataclass(frozen=True, slots=True)
class _HTTPResult(BaseHTTPResult):
    response: httpx2.Response | None = field(default=None, repr=False)

    def content(self) -> bytes:
        return self.response.content if self.response is not None else b""

    def headers(self) -> Mapping[str, str]:
        return self.response.headers if self.response is not None else {}


class OidcOTelTransport(BaseHTTPTransport):
    """Keep the exporter on the same TLS/auth path as other Vibe requests."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        data: bytes | None = None,
    ) -> BaseHTTPResult:
        try:
            response = run_sync(self._request(method, url, headers, timeout, data))
        except Exception:
            # Export errors can reach OTel logs: never retain HTTP/token payloads.
            return _HTTPResult(error=RuntimeError("OIDC trace export failed"))
        return _HTTPResult(
            status_code=response.status_code,
            reason=response.reason_phrase,
            response=response,
        )

    async def _request(
        self,
        method: str,
        url: str,
        headers: dict[str, str] | None,
        timeout: float | None,
        data: bytes | None,
    ) -> httpx2.Response:
        async with VibeAsyncHTTPClient(
            verify=build_ssl_context(), follow_redirects=False
        ) as client:
            return await client.request(
                method, url, headers=headers, timeout=timeout, content=data
            )

    def close(self) -> None:
        pass

    def is_connection_error(self, exception: Exception | None) -> bool:
        return False
