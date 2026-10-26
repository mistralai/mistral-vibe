from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import httpx

from vibe.core.platform_auth._credentials_port import (
    PlatformCredentialProvider,
    PlatformCredentialRequired,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
    _path_has_ambiguous_segments,
)
from vibe.utils.http import VibeAsyncHTTPClient, build_ssl_context


class PlatformCredentialUnavailable(Exception):
    def __init__(self, required: PlatformCredentialRequired) -> None:
        super().__init__(f"Platform credential is {required.reason}")
        self.required = required


class PlatformCredentialHeaderConflict(ValueError):
    pass


class UntrustedPlatformDestination(ValueError):
    pass


class _PlatformAuth(httpx.Auth):
    def __init__(
        self, target: PlatformCredentialTarget, provider: PlatformCredentialProvider
    ) -> None:
        self._target = target
        self._provider = provider
        self._base_url = httpx.URL(target.base_url)

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        if not _is_in_scope(request.url, self._base_url):
            raise UntrustedPlatformDestination(
                f"Platform credential cannot authorize {request.url}"
            )

        resolved = await self._provider.resolve(self._target)
        if isinstance(resolved, PlatformCredentialRequired):
            raise PlatformCredentialUnavailable(resolved)
        _inject_headers(request, resolved)

        response = yield request
        if response.status_code == httpx.codes.UNAUTHORIZED:
            await self._provider.reject(
                self._target, observed=resolved.identity, reason="http_unauthorized"
            )


class MistralPlatformHTTPClient(VibeAsyncHTTPClient):
    """HTTP client authorized only for one configured Mistral platform base URL."""

    def __init__(
        self,
        *,
        base_url: str,
        credentials: PlatformCredentialProvider,
        audience: str | None = None,
        **kwargs: Any,
    ) -> None:
        target = PlatformCredentialTarget(base_url=base_url, audience=audience)
        if "auth" in kwargs:
            raise ValueError("MistralPlatformHTTPClient owns request authentication")
        if "verify" in kwargs:
            raise ValueError("MistralPlatformHTTPClient owns TLS verification")
        kwargs["follow_redirects"] = False
        super().__init__(
            base_url=target.base_url,
            auth=_PlatformAuth(target, credentials),
            verify=build_ssl_context(),
            **kwargs,
        )

    async def send(
        self,
        request: httpx.Request,
        *,
        stream: bool = False,
        auth: Any = httpx.USE_CLIENT_DEFAULT,
        follow_redirects: Any = httpx.USE_CLIENT_DEFAULT,
    ) -> httpx.Response:
        if auth is not httpx.USE_CLIENT_DEFAULT:
            raise ValueError("MistralPlatformHTTPClient owns request authentication")
        if follow_redirects is True:
            raise ValueError("MistralPlatformHTTPClient does not follow redirects")
        return await super().send(
            request, stream=stream, auth=auth, follow_redirects=False
        )


def _is_in_scope(url: httpx.URL, base_url: httpx.URL) -> bool:
    if _origin(url) != _origin(base_url):
        return False
    raw_path = url.raw_path.partition(b"?")[0]
    if _path_has_ambiguous_segments(raw_path):
        return False
    base_path = base_url.path.rstrip("/")
    return (
        not base_path or url.path == base_path or url.path.startswith(f"{base_path}/")
    )


def _origin(url: httpx.URL) -> tuple[str, str | None, int | None]:
    default_port = {"http": 80, "https": 443}.get(url.scheme)
    port = url.port if url.port is not None else default_port
    return url.scheme, url.host, port


def _inject_headers(
    request: httpx.Request, snapshot: PlatformCredentialSnapshot
) -> None:
    for name, value in snapshot.headers.items():
        if name in request.headers:
            raise PlatformCredentialHeaderConflict(
                f"Request already defines credential header {name!r}"
            )
        request.headers[name] = value


__all__ = [
    "MistralPlatformHTTPClient",
    "PlatformCredentialHeaderConflict",
    "PlatformCredentialUnavailable",
    "UntrustedPlatformDestination",
]
