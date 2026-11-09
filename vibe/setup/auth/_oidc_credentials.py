from __future__ import annotations

import asyncio
import os
from threading import RLock
from typing import Literal, cast

import keyring
from keyring.errors import KeyringError, PasswordDeleteError
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, field_serializer

_KEYRING_SERVICE = "ai.mistral.vibe.oidc"
_KEYRING_ENTRY = "cli-oidc:v1:tokens"
_DISABLE_KEYRING_ENV_VAR = "VIBE_TEST_DISABLE_KEYRING"
# One credential slot is shared by service instances and worker event loops.
_slot_lock = RLock()
_UNLOADED = object()
# Process-wide copy of the slot, so hot paths do not read the keyring each time.
_cached: OidcTokens | None | object = _UNLOADED


class OidcTenant(BaseModel):
    """The organization and workspace the signed-in identity acts in.

    Not secret. ``selection`` is the workspace override it was discovered for
    (``""`` for the default), so a changed override discovers again.
    """

    model_config = ConfigDict(extra="forbid")

    selection: str
    organization_id: str
    workspace_id: str


class OidcTokens(BaseModel):
    """Versioned OIDC credentials; JSON serialization exposes secrets for storage."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    issuer: str
    client_id: str
    audience: str
    subject: str
    access_token: SecretStr
    refresh_token: SecretStr | None
    id_token: SecretStr
    expires_at: float
    nonce: str | None = None
    id_token_expires_at: float | None = None
    tenant: OidcTenant | None = None

    @field_serializer("access_token", "refresh_token", "id_token", when_used="json")
    def _serialize_secret(self, value: SecretStr | None) -> str | None:
        return value.get_secret_value() if value is not None else None

    def same_identity(self, other: OidcTokens) -> bool:
        return (self.issuer, self.client_id, self.audience, self.subject) == (
            other.issuer,
            other.client_id,
            other.audience,
            other.subject,
        )


class OidcStorageError(Exception):
    """OIDC persistence failed without exposing backend details or stored secrets."""


class OidcCredentialStore:
    """Persist OIDC tokens in their own keyring slot, without API-key fallbacks."""

    async def load(self) -> OidcTokens | None:
        return await asyncio.to_thread(self._load)

    async def reload(self) -> OidcTokens | None:
        """Read the keyring itself, e.g. before a refresh another process may have done."""
        return await asyncio.to_thread(self._reload)

    async def save(
        self, tokens: OidcTokens, *, complete_on_cancel: bool = False
    ) -> None:
        await self._wait_for_worker(
            asyncio.create_task(asyncio.to_thread(self._save_locked, tokens)),
            complete_on_cancel=complete_on_cancel,
        )

    async def save_if_current(
        self, tokens: OidcTokens, *, expected: OidcTokens
    ) -> bool:
        return await self._wait_for_worker(
            asyncio.create_task(
                asyncio.to_thread(self._save_if_current, tokens, expected)
            )
        )

    async def clear(self) -> None:
        await self._wait_for_worker(
            asyncio.create_task(asyncio.to_thread(self._clear_locked))
        )

    def _save_locked(self, tokens: OidcTokens) -> None:
        with _slot_lock:
            self._save(tokens)

    def save_tenant(self, tokens: OidcTokens, tenant: OidcTenant) -> None:
        """Record ``tenant`` on the stored session, if it is still ``tokens``'s
        identity: a sign-out or another account's sign-in is not overwritten.
        """
        with _slot_lock:
            current = self._reload()
            if current is None or not current.same_identity(tokens):
                return
            self._save(current.model_copy(update={"tenant": tenant}))

    def _save_if_current(self, tokens: OidcTokens, expected: OidcTokens) -> bool:
        with _slot_lock:
            current = self._reload()
            # The tenant is not token material: a tenant recorded during the
            # refresh is kept rather than failing it.
            if (
                current is None
                or current.model_copy(update={"tenant": expected.tenant}) != expected
            ):
                return False
            self._save(tokens.model_copy(update={"tenant": current.tenant}))
            return True

    def _clear_locked(self) -> None:
        with _slot_lock:
            self._clear()

    async def _wait_for_worker[T](
        self, task: asyncio.Task[T], *, complete_on_cancel: bool = False
    ) -> T:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # Keep the caller's lock held until the worker can no longer mutate storage.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if complete_on_cancel and not task.cancelled():
                # Persistence is the commit point: report the finished write outcome.
                return task.result()
            if not task.cancelled():
                task.exception()
            raise

    def _load(self) -> OidcTokens | None:
        if os.environ.get(_DISABLE_KEYRING_ENV_VAR) == "1":
            return None
        with _slot_lock:
            if _cached is not _UNLOADED:
                return cast(OidcTokens | None, _cached)
            tokens = self._read()
            _set_cached(tokens)
            return tokens

    def _reload(self) -> OidcTokens | None:
        with _slot_lock:
            _set_cached(_UNLOADED)
            return self._load()

    def _read(self) -> OidcTokens | None:
        try:
            raw = keyring.get_password(_KEYRING_SERVICE, _KEYRING_ENTRY)
        except (KeyringError, ImportError):
            raise OidcStorageError(
                "Could not read OIDC credentials from keyring"
            ) from None
        if raw is None:
            return None
        try:
            return OidcTokens.model_validate_json(raw)
        except ValidationError:
            # Validation errors can embed the entire stored token bundle.
            raise OidcStorageError("Stored OIDC credentials are invalid") from None

    def _save(self, tokens: OidcTokens) -> None:
        if os.environ.get(_DISABLE_KEYRING_ENV_VAR) == "1":
            raise OidcStorageError("OIDC credential storage is disabled")
        _set_cached(_UNLOADED)
        try:
            keyring.set_password(
                _KEYRING_SERVICE, _KEYRING_ENTRY, tokens.model_dump_json()
            )
        except (KeyringError, ImportError):
            raise OidcStorageError(
                "Could not save OIDC credentials to keyring"
            ) from None
        _set_cached(tokens)

    def _clear(self) -> None:
        if os.environ.get(_DISABLE_KEYRING_ENV_VAR) == "1":
            raise OidcStorageError("OIDC credential storage is disabled")
        _set_cached(_UNLOADED)
        try:
            keyring.delete_password(_KEYRING_SERVICE, _KEYRING_ENTRY)
        except PasswordDeleteError:
            # macOS wraps access-denied errors too; only a confirmed miss is safe.
            try:
                remaining = keyring.get_password(_KEYRING_SERVICE, _KEYRING_ENTRY)
            except (KeyringError, ImportError):
                raise OidcStorageError(
                    "Could not verify removal of OIDC credentials from keyring"
                ) from None
            if remaining is not None:
                raise OidcStorageError(
                    "Could not remove OIDC credentials from keyring"
                ) from None
        except (KeyringError, ImportError):
            raise OidcStorageError(
                "Could not remove OIDC credentials from keyring"
            ) from None
        _set_cached(None)


def _set_cached(value: OidcTokens | None | object) -> None:
    global _cached
    _cached = value


def clear_oidc_token_cache() -> None:
    """Forget the in-memory copy; the next read goes to the keyring."""
    with _slot_lock:
        _set_cached(_UNLOADED)


__all__ = [
    "OidcCredentialStore",
    "OidcStorageError",
    "OidcTenant",
    "OidcTokens",
    "clear_oidc_token_cache",
]
