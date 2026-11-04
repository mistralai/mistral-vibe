"""``platformCredentials/headers``: a CLI client's first-party request headers.

The Rust TUI sends its own audio requests. In CLI OIDC mode it has no token,
so it asks for the headers of the connection's credential, per URL; the
provider decides which destinations may receive them. API-key clients read
their key themselves and are refused.
"""

from __future__ import annotations

from vibe.app_server._connection_protocol import ClientInfo
from vibe.app_server._credential_selection import MISTRAL_API_KEY_ENV, uses_cli_oidc
from vibe.app_server._dispatch import RequestFailure
from vibe.app_server._model import ProtocolModel, validate_wire
from vibe.app_server.protocol import ProtocolErrorCode
from vibe.core.platform_auth import (
    PlatformCredentialFactory,
    PlatformCredentialRequired,
)
from vibe.core.platform_auth._mistral import resolve_snapshot

METHOD = "platformCredentials/headers"


class PlatformCredentialHeadersParams(ProtocolModel):
    url: str


class PlatformCredentialHeadersResponse(ProtocolModel):
    headers: dict[str, str]


async def platform_credential_headers(
    raw_params: dict[str, object],
    *,
    client: ClientInfo | None,
    desktop_credentials: bool,
    credentials: PlatformCredentialFactory,
) -> PlatformCredentialHeadersResponse:
    params = validate_wire(PlatformCredentialHeadersParams, raw_params)
    if desktop_credentials or not uses_cli_oidc(client, MISTRAL_API_KEY_ENV):
        raise RequestFailure(
            ProtocolErrorCode.FORBIDDEN,
            "Platform credential headers are only served to CLI OIDC clients",
        )
    try:
        snapshot = await resolve_snapshot(credentials(MISTRAL_API_KEY_ENV), params.url)
    except Exception:
        # Raw token, keyring and discovery errors may contain secrets.
        raise RequestFailure(
            ProtocolErrorCode.INTERNAL_ERROR, "Platform credential resolution failed"
        ) from None
    if isinstance(snapshot, PlatformCredentialRequired):
        raise RequestFailure(
            ProtocolErrorCode.UNAUTHORIZED,
            f"Platform credential is {snapshot.reason}; sign in again",
        )
    return PlatformCredentialHeadersResponse(headers=dict(snapshot.headers))


__all__ = [
    "METHOD",
    "PlatformCredentialHeadersParams",
    "PlatformCredentialHeadersResponse",
    "platform_credential_headers",
]
