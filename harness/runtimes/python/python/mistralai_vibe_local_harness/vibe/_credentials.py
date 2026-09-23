"""SDK-independent contracts for Unified Harness provider credentials.

The Harness never resolves a provider credential itself: reading an OS keyring,
a ``.env`` file, or Google's application-default chain is host product state,
and interpreting a provider's key environment variable or API style is host
configuration. The Host implements this port and hands back resolved material;
the Harness only asks for it at the point of a model call and reports back when
a provider refused it.

Shaped after ``MCPAuthorizationProvider`` in ``_mcp_models``: a ``resolve`` that
returns either a snapshot or an "authorization required" state, and a ``reject``
that carries the revision the caller actually used so a concurrent refresh is
not discarded by a stale report.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import sha256
from types import MappingProxyType
from typing import Literal, Protocol

type ProviderAuthReason = Literal["missing", "expired", "rejected"]
type ProviderRejectionReason = Literal["http_unauthorized", "http_forbidden"]


@dataclass(frozen=True, slots=True)
class ProviderCredentialSnapshot:
    """One resolution of the active provider's credential.

    ``token`` and ``headers`` describe the same credential in the two shapes
    provider clients need: an SDK that takes a key reads ``token``, a raw HTTP
    adapter merges ``headers``. Both are always populated when the scheme
    supports them; neither is ever logged or persisted.
    """

    token: str | None = field(repr=False)
    headers: Mapping[str, str] = field(repr=False)
    revision: str
    expires_at: datetime | None = None
    # Where the Host read this credential from, phrased for an error message:
    # "env var MISTRAL_API_KEY", "the keyring". ``None`` for a static or minted
    # token, whose origin says nothing a user could act on. Carried so a
    # rejection can tell the user which of the two to fix.
    api_key_source: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "headers", MappingProxyType(dict(self.headers)))


@dataclass(frozen=True, slots=True)
class ProviderAuthRequired:
    """No usable credential exists; interactive sign-in is needed.

    Not an error — a state. The Harness turns it into a typed completion
    failure rather than letting it escape as an exception.
    """

    reason: ProviderAuthReason
    provider: str
    message: str


type ProviderCredentialResult = ProviderCredentialSnapshot | ProviderAuthRequired


class ProviderCredentialProvider(Protocol):
    async def resolve(self) -> ProviderCredentialResult: ...

    async def reject(
        self, *, observed_revision: str, reason: ProviderRejectionReason
    ) -> None: ...


class StaticProviderCredentials:
    """Default implementation: a fixed key, for standalone and test use.

    A ``None`` token resolves to a snapshot whose token is ``None`` rather than
    to ``ProviderAuthRequired`` because a standalone Harness has no sign-in to
    offer and the provider client should report the missing credential.
    """

    def __init__(self, token: str | None = None) -> None:
        self._token = token

    async def resolve(self) -> ProviderCredentialResult:
        return ProviderCredentialSnapshot(
            token=self._token,
            headers=bearer_headers(self._token),
            revision=static_revision(self._token),
        )

    async def reject(
        self, *, observed_revision: str, reason: ProviderRejectionReason
    ) -> None:
        # A static key has nowhere to be invalidated to: the next resolution
        # would produce the same material, so recording the rejection would
        # only turn one refused turn into a permanently dead session.
        del observed_revision, reason


def bearer_headers(token: str | None) -> Mapping[str, str]:
    return {"Authorization": f"Bearer {token}"} if token else {}


def static_revision(token: str | None) -> str:
    """An opaque, stable, secret-free identifier for static material."""
    if not token:
        return "static:none"
    return f"static:{sha256(token.encode()).hexdigest()[:16]}"


__all__ = [
    "ProviderAuthReason",
    "ProviderAuthRequired",
    "ProviderCredentialProvider",
    "ProviderCredentialResult",
    "ProviderCredentialSnapshot",
    "ProviderRejectionReason",
    "StaticProviderCredentials",
    "bearer_headers",
    "static_revision",
]
