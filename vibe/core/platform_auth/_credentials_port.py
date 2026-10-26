from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Literal, Protocol
from urllib.parse import unquote_to_bytes, urlsplit, urlunsplit

type PlatformCredentialKind = Literal["api_key", "oauth_access_token"]


@dataclass(frozen=True, slots=True)
class PlatformCredentialIdentity:
    """Non-secret identity of one resolved credential snapshot."""

    kind: PlatformCredentialKind
    cache_namespace: str
    revision: str


@dataclass(frozen=True, slots=True)
class PlatformCredentialTarget:
    """The first-party platform destination a credential may authorize."""

    base_url: str
    audience: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", _normalize_base_url(self.base_url))


@dataclass(frozen=True, slots=True)
class PlatformCredentialSnapshot:
    """Immutable authorization material resolved for one outbound request.

    ``cache_namespace`` is a stable, non-secret provider identity. ``revision``
    is opaque to consumers, collision-resistant within that namespace, and must
    change whenever the effective credential changes.
    """

    kind: PlatformCredentialKind
    headers: Mapping[str, str] = field(repr=False)
    cache_namespace: str
    revision: str
    expires_at: datetime | None = None
    source: str | None = None

    def __post_init__(self) -> None:
        if not self.cache_namespace:
            raise ValueError("Credential cache namespace must not be empty")
        if not self.revision:
            raise ValueError("Credential revision must not be empty")
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))

    @property
    def identity(self) -> PlatformCredentialIdentity:
        return PlatformCredentialIdentity(
            kind=self.kind, cache_namespace=self.cache_namespace, revision=self.revision
        )


@dataclass(frozen=True, slots=True)
class PlatformCredentialRequired:
    reason: Literal[
        "missing",
        "expired",
        "rejected",
        "invalid",
        "reauthentication_required",
        "transient",
    ]
    observed_revision: str | None = None


type PlatformCredentialResult = PlatformCredentialSnapshot | PlatformCredentialRequired


class PlatformCredentialProvider(Protocol):
    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult: ...

    async def reject(
        self,
        target: PlatformCredentialTarget,
        *,
        observed: PlatformCredentialIdentity,
        reason: Literal["http_unauthorized"],
    ) -> PlatformCredentialResult: ...


def _normalize_base_url(value: str) -> str:
    # ProviderConfig.api_base is intentionally a plain string, so validate its
    # trust boundary here before it can select where authorization is attached.
    parsed = urlsplit(value)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Platform base URL has an invalid port") from exc
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ValueError("Platform base URL must be an HTTP(S) origin and path")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Platform base URL must be an HTTP(S) origin and path")
    if parsed.query or parsed.fragment:
        raise ValueError("Platform base URL must be an HTTP(S) origin and path")
    if _path_has_ambiguous_segments(parsed.path):
        raise ValueError("Platform base URL contains an ambiguous path")
    path = f"{parsed.path.rstrip('/')}/"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _path_has_ambiguous_segments(raw_path: str | bytes) -> bool:
    """Reject path spellings intermediaries may decode into another route."""
    path_bytes = raw_path if isinstance(raw_path, bytes) else raw_path.encode()
    for raw_segment in path_bytes.split(b"/"):
        segment = unquote_to_bytes(raw_segment)
        while True:
            if segment in {b".", b".."} or b"/" in segment or b"\\" in segment:
                return True
            decoded = unquote_to_bytes(segment)
            if decoded == segment:
                break
            segment = decoded
    return False


__all__ = [
    "PlatformCredentialIdentity",
    "PlatformCredentialKind",
    "PlatformCredentialProvider",
    "PlatformCredentialRequired",
    "PlatformCredentialResult",
    "PlatformCredentialSnapshot",
    "PlatformCredentialTarget",
]
