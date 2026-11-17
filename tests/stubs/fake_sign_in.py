from __future__ import annotations

from typing import Any

from mistralai_vibe_local_harness.vibe import (
    ProviderAuthRequired,
    ProviderCredentialResult,
    ProviderCredentialSnapshot,
    ProviderRejectionReason,
)
from vibe.core.llm.utility_credentials import UtilityCredentials
from vibe.core.platform_auth import PlatformCredentialSnapshot, PlatformCredentialTarget

SIGNED_IN_PRINCIPAL = "oidc:v1:signed-in-user"
SIGNED_IN_HEADERS = {
    "Authorization": "Bearer desktop-id-token",
    "x-external-organization-id": "org-1",
}


class FakeSignIn:
    """A connection's sign-in (Desktop's ID token), never an API key.

    Knows offline whether a user signed in and which account; resolving it
    returns the ID token with its tenant headers, and is counted.
    """

    def __init__(
        self, *, signed_in: bool = True, principal: str | None = SIGNED_IN_PRINCIPAL
    ) -> None:
        self.signed_in = signed_in
        self.principal = principal
        self.resolved = 0

    def __call__(self, _env_var: str) -> FakeSignIn:
        return self

    def has_credential(self) -> bool:
        return self.signed_in

    def cached_principal(self) -> str | None:
        return self.principal if self.signed_in else None

    async def resolve(self, target: PlatformCredentialTarget) -> Any:
        self.resolved += 1
        return PlatformCredentialSnapshot(
            kind="oidc_id_token",
            headers=SIGNED_IN_HEADERS,
            cache_namespace="oidc_id_token:desktop_connection",
            revision="desktop-id-1",
            principal=self.principal,
        )

    async def reject(self, target: PlatformCredentialTarget, **_: Any) -> Any:
        return await self.resolve(target)


class FakeSessionCredential:
    """The session's credential for one provider, as its model calls send it."""

    def __init__(self, sign_in: FakeSignIn) -> None:
        self._sign_in = sign_in
        self.rejected: list[tuple[str, str]] = []

    async def resolve(self) -> ProviderCredentialResult:
        if not self._sign_in.signed_in:
            return ProviderAuthRequired(
                reason="missing", provider="mistral", message="Not signed in"
            )
        return ProviderCredentialSnapshot(
            token="desktop-id-token",
            headers=SIGNED_IN_HEADERS,
            revision="mistral:desktop-id-1",
            follow_redirects=False,
        )

    async def reject(
        self, *, observed_revision: str, reason: ProviderRejectionReason
    ) -> None:
        self.rejected.append((observed_revision, reason))


def signed_in_credentials(sign_in: FakeSignIn | None = None) -> UtilityCredentials:
    """``UtilityCredentials`` for a connection signed in with ``sign_in``."""
    sign_in = sign_in or FakeSignIn()
    session_credential = FakeSessionCredential(sign_in)
    return UtilityCredentials(
        platform=sign_in, for_provider=lambda _name: session_credential
    )
