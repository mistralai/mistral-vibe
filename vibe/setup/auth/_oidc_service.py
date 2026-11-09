from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import secrets
import time
from typing import Literal, cast

from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
import httpx2

from vibe.core.platform_auth import MistralPlatformHTTPClient, PlatformCredentialTarget
from vibe.setup.auth._oidc_callback import OidcCallback
from vibe.setup.auth._oidc_client import OidcClient
from vibe.setup.auth._oidc_config import OidcConfig, OidcError, require_oidc_enabled
from vibe.setup.auth._oidc_credentials import (
    OidcCredentialStore,
    OidcStorageError,
    OidcTokens,
)
from vibe.setup.auth._oidc_platform_credentials import OidcPlatformCredentialProvider
from vibe.utils.http import build_ssl_context


@dataclass(slots=True)
class OidcAttempt:
    attempt_id: str
    authorization_url: str | None = None
    status: Literal["waiting", "exchanging", "completed", "failed"] = "waiting"
    detail: str | None = None
    error_type: Literal["oidc_error", "storage_error", "unexpected_error"] | None = None
    task: asyncio.Task[None] | None = None
    started_at: float = field(default_factory=time.monotonic)
    started_timestamp: float = field(default_factory=time.time)
    _done_notified: bool = False


class OidcService:
    """One active login and serialized refresh-on-access per app server."""

    def __init__(self, client: OidcClient, store: OidcCredentialStore) -> None:
        self.client = client
        self.store = store
        self._lock = asyncio.Lock()
        self._attempt: OidcAttempt | None = None
        self.on_attempt_started: Callable[[OidcAttempt], None] | None = None
        self.on_attempt_done: (
            Callable[[OidcAttempt, Literal["success", "error", "cancelled"]], None]
            | None
        ) = None

    @classmethod
    def from_env(cls) -> OidcService:
        return cls(OidcClient(OidcConfig.from_env()), OidcCredentialStore())

    async def start(self) -> OidcAttempt:
        require_oidc_enabled()
        async with self._lock:
            await self.cancel()
            attempt = OidcAttempt(secrets.token_urlsafe(24))
            self._attempt = attempt
            if self.on_attempt_started is not None:
                try:
                    self.on_attempt_started(attempt)
                except Exception:
                    pass
            attempt.task = asyncio.create_task(self._complete(attempt))
            return attempt

    async def _complete(self, attempt: OidcAttempt) -> None:
        session: AsyncOAuth2Client | None = None
        callback: OidcCallback | None = None
        try:
            async with asyncio.timeout(300):
                require_oidc_enabled()
                session = self.client.session()
                metadata = await self.client.discover(session)
                require_oidc_enabled()
                verifier, nonce, state = (
                    secrets.token_urlsafe(64),
                    secrets.token_urlsafe(32),
                    secrets.token_urlsafe(32),
                )
                callback = OidcCallback(self.client.config.redirect_uri, state)
                await callback.__aenter__()
                extra = (
                    {"audience": self.client.config.audience}
                    if self.client.config.audience
                    else {}
                )
                attempt.authorization_url, _ = session.create_authorization_url(
                    metadata.authorization_endpoint,
                    state=state,
                    code_verifier=verifier,
                    nonce=nonce,
                    **extra,
                )
                response = await callback.result
                require_oidc_enabled()
                attempt.status = "exchanging"
                raw = await session.fetch_token(
                    metadata.token_endpoint,
                    authorization_response=response,
                    state=callback.state,
                    code_verifier=verifier,
                )
                tokens = await self.client.validated_tokens(
                    session, metadata, raw, nonce=nonce
                )
                async with self._lock:
                    require_oidc_enabled()
                    await self.store.save(tokens, complete_on_cancel=True)
                attempt.status = "completed"
        except asyncio.CancelledError:
            raise
        except OidcError as exc:
            attempt.status = "failed"
            attempt.detail = str(exc)
            attempt.error_type = "oidc_error"
        except Exception as exc:
            # Library/HTTP/storage errors can contain codes or token payloads.
            attempt.status = "failed"
            attempt.error_type = (
                "storage_error"
                if isinstance(exc, OidcStorageError)
                else "unexpected_error"
            )
            attempt.detail = "OIDC sign-in failed; check issuer, client registration and KeyRing availability"
        finally:
            self._notify_attempt_done(attempt)
            try:
                if callback is not None:
                    await callback.__aexit__()
            finally:
                if session is not None:
                    await cast(httpx2.AsyncClient, session).aclose()

    def _notify_attempt_done(self, attempt: OidcAttempt) -> None:
        if self.on_attempt_done is None or attempt._done_notified:
            return
        attempt._done_notified = True
        status = (
            "success"
            if attempt.status == "completed"
            else "error"
            if attempt.status == "failed"
            else "cancelled"
        )
        try:
            self.on_attempt_done(attempt, status)
        except Exception:
            # Analytics must not change authentication or resource cleanup.
            pass

    def poll(self, attempt_id: str) -> OidcAttempt:
        require_oidc_enabled()
        if self._attempt is None or self._attempt.attempt_id != attempt_id:
            raise OidcError("Unknown OIDC sign-in attempt")
        return self._attempt

    async def cancel(self, attempt_id: str | None = None) -> None:
        attempt = self._attempt
        if attempt is None or (
            attempt_id is not None and attempt.attempt_id != attempt_id
        ):
            return
        self._attempt = None
        if attempt.task is not None:
            attempt.task.cancel()
            await asyncio.gather(attempt.task, return_exceptions=True)
        self._notify_attempt_done(attempt)

    async def clear(self) -> None:
        require_oidc_enabled()
        async with self._lock:
            await self.cancel()
            await self.store.clear()

    async def access_tokens(self, *, force_refresh: bool = False) -> OidcTokens:
        require_oidc_enabled()
        async with self._lock:
            tokens = await self.store.load()
            if force_refresh or tokens is None or tokens.expires_at <= time.time() + 30:
                # The in-memory copy may predate another process's rotation.
                tokens = await self.store.reload()
            config = self.client.config
            if tokens is None:
                raise OidcError("OIDC re-login required: no stored credentials")
            if (tokens.issuer, tokens.client_id, tokens.audience) != (
                config.issuer,
                config.client_id,
                config.audience,
            ):
                raise OidcError(
                    "OIDC re-login required: credential configuration changed"
                )
            if not force_refresh and tokens.expires_at > time.time() + 30:
                return tokens
            if tokens.refresh_token is None:
                raise OidcError("OIDC re-login required: no refresh token")
            session = self.client.session()
            async with cast(httpx2.AsyncClient, session):
                metadata = await self.client.discover(session)
                try:
                    raw = await session.refresh_token(
                        metadata.token_endpoint,
                        refresh_token=tokens.refresh_token.get_secret_value(),
                        scope=None,
                    )
                except OAuthError as exc:
                    if exc.error == "invalid_grant":
                        raise OidcError(
                            "OIDC re-login required: refresh token rejected"
                        ) from None
                    raise OidcError(
                        "OIDC refresh failed; retry the probe later"
                    ) from None
                updated = await self.client.validated_tokens(
                    session, metadata, raw, previous=tokens
                )
            require_oidc_enabled()
            if not await self.store.save_if_current(updated, expected=tokens):
                raise OidcError(
                    "OIDC credentials changed during refresh; retry or re-login"
                )
            return updated

    async def probe(self) -> dict[str, str]:
        require_oidc_enabled()
        session = self.client.session()
        async with cast(httpx2.AsyncClient, session):
            metadata = await self.client.discover(session)
        if metadata.userinfo_endpoint is None:
            raise OidcError("OIDC issuer does not advertise a userinfo endpoint")
        # OpenID Connect Core 1.0 section 5.3: UserInfo uses the access token.
        target = PlatformCredentialTarget(
            base_url=metadata.userinfo_endpoint, audience=self.client.config.audience
        )
        credentials = OidcPlatformCredentialProvider(
            target, self.access_tokens, config=self.client.config
        )
        async with MistralPlatformHTTPClient(
            base_url=target.base_url,
            audience=target.audience,
            credentials=credentials,
            timeout=20,
            transport=httpx2.AsyncHTTPTransport(
                verify=build_ssl_context(), trust_env=False
            ),
        ) as client:
            response = await client.get(metadata.userinfo_endpoint)
        if response.status_code == httpx2.codes.UNAUTHORIZED:
            raise OidcError("OIDC access token rejected; re-login required")
        response.raise_for_status()
        identity = response.json()
        if identity.get("sub") != credentials.subject:
            raise OidcError("OIDC UserInfo subject mismatch")
        return {
            "issuer": self.client.config.issuer,
            "subject": credentials.subject or "",
        }
