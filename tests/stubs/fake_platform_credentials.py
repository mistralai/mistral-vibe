"""A Mistral platform credential whose answers a test sets."""

from __future__ import annotations

import asyncio
from typing import Any

from vibe.core.platform_auth import (
    PlatformCredentialRequired,
    PlatformCredentialResult,
    PlatformCredentialSnapshot,
    PlatformCredentialTarget,
)


class FakePlatformCredentials:
    """A credential and its factory: ``credentials=fake`` hands out ``fake``.

    ``principal`` is what ``cached_principal`` knows without network; a
    resolve returns ``unavailable`` when set, else a snapshot naming
    ``resolved_principal`` (``principal`` by default). ``resolve_delay_s``
    holds each resolve that long.
    """

    def __init__(
        self,
        principal: str | None = "oidc:v1:user-a",
        *,
        signed_in: bool = True,
        unavailable: PlatformCredentialRequired | None = None,
        resolved_principal: str | None = None,
        resolve_delay_s: float = 0.0,
    ) -> None:
        self.principal = principal
        self.signed_in = signed_in
        self.unavailable = unavailable
        self.resolved_principal = resolved_principal
        self.resolve_delay_s = resolve_delay_s
        self.resolved = 0

    def __call__(self, _api_key_env_var: str) -> FakePlatformCredentials:
        return self

    def has_credential(self) -> bool:
        return self.signed_in

    def cached_principal(self) -> str | None:
        return self.principal if self.signed_in else None

    async def resolve(
        self, target: PlatformCredentialTarget
    ) -> PlatformCredentialResult:
        self.resolved += 1
        if self.resolve_delay_s:
            await asyncio.sleep(self.resolve_delay_s)
        if self.unavailable is not None:
            return self.unavailable
        return PlatformCredentialSnapshot(
            kind="oidc_id_token",
            headers={"Authorization": "Bearer fake-id-token"},
            cache_namespace="oidc_id_token:fake",
            revision="fake-1",
            principal=self.resolved_principal or self.principal,
        )

    async def reject(self, target: PlatformCredentialTarget, **_: Any) -> Any:
        return PlatformCredentialRequired(reason="rejected")


__all__ = ["FakePlatformCredentials"]
