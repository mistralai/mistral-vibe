from __future__ import annotations

from collections.abc import Mapping
import math
import time
from typing import Any
from urllib.parse import urlsplit

from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.oidc.core import CodeIDToken
from joserfc import jwt
from joserfc.jwk import KeySet
from pydantic import BaseModel, ConfigDict, SecretStr

from vibe.setup.auth._oidc_config import OidcConfig, OidcError, validate_oidc_url
from vibe.setup.auth._oidc_credentials import OidcTokens
from vibe.utils.http import build_ssl_context


class OidcMetadata(BaseModel):
    model_config = ConfigDict(extra="ignore")

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: str | None = None
    id_token_signing_alg_values_supported: list[str] = ["RS256"]


class TokenResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    access_token: SecretStr
    token_type: str
    expires_in: float
    refresh_token: SecretStr | None = None
    id_token: SecretStr | None = None


class OidcClient:
    """Authlib public-client code flow; token consumers remain isolated."""

    def __init__(self, config: OidcConfig) -> None:
        self.config = config

    def session(self) -> AsyncOAuth2Client:
        return AsyncOAuth2Client(
            client_id=self.config.client_id,
            token_endpoint_auth_method="none",
            redirect_uri=self.config.redirect_uri,
            scope=self.config.scope,
            code_challenge_method="S256",
            verify=build_ssl_context(),
            follow_redirects=False,
            timeout=20,
            trust_env=False,
        )

    async def discover(self, session: AsyncOAuth2Client) -> OidcMetadata:
        response = await session.request(
            "GET",
            f"{self.config.issuer.rstrip('/')}/.well-known/openid-configuration",
            withhold_token=True,
        )
        response.raise_for_status()
        metadata = OidcMetadata.model_validate(response.json())
        if metadata.issuer != self.config.issuer:
            raise OidcError("OIDC discovery issuer mismatch")
        for endpoint in (
            metadata.authorization_endpoint,
            metadata.token_endpoint,
            metadata.jwks_uri,
            metadata.userinfo_endpoint,
        ):
            if endpoint is not None:
                validate_oidc_url(endpoint)
        if metadata.userinfo_endpoint is not None:
            issuer_url = urlsplit(self.config.issuer)
            userinfo_url = urlsplit(metadata.userinfo_endpoint)
            if (userinfo_url.scheme, userinfo_url.netloc) != (
                issuer_url.scheme,
                issuer_url.netloc,
            ):
                raise OidcError("OIDC UserInfo must belong to the issuer origin")
        return metadata

    async def validated_tokens(
        self,
        session: AsyncOAuth2Client,
        metadata: OidcMetadata,
        raw: Mapping[str, Any],
        *,
        nonce: str | None = None,
        previous: OidcTokens | None = None,
    ) -> OidcTokens:
        # Authlib 1.9's OAuth2Client.parse_response_token returns an OAuth2Token
        # mapping; the response fields are defined by RFC 6749 section 5.1.
        token = TokenResponse.model_validate(raw)
        if (
            token.token_type.lower() != "bearer"
            or not token.access_token.get_secret_value()
            or not math.isfinite(token.expires_in)
            or token.expires_in <= 0
        ):
            raise OidcError("OIDC token response is invalid")
        if token.id_token is None and previous is None:
            raise OidcError("OIDC response did not include an ID token")
        subject = previous.subject if previous is not None else ""
        id_token = previous.id_token if previous is not None else SecretStr("")
        id_token_expires_at = (
            previous.id_token_expires_at if previous is not None else None
        )
        if token.id_token is not None:
            response = await session.request(
                "GET", metadata.jwks_uri, withhold_token=True
            )
            response.raise_for_status()
            keys = KeySet.import_key_set(response.json())
            allowed = [
                alg
                for alg in metadata.id_token_signing_alg_values_supported
                if alg in {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
            ]
            if not allowed:
                raise OidcError(
                    "OIDC issuer has no supported ID-token signing algorithm"
                )
            decoded = jwt.decode(
                token.id_token.get_secret_value(), keys, algorithms=allowed
            )
            expected_nonce = nonce
            if previous is not None and "nonce" in decoded.claims:
                expected_nonce = previous.nonce
                if expected_nonce is None:
                    raise OidcError(
                        "OIDC re-login required: original nonce is unavailable"
                    )
            claims = CodeIDToken(
                decoded.claims,
                decoded.header,
                options={
                    "iss": {"essential": True, "value": self.config.issuer},
                    "aud": {"essential": True, "value": self.config.client_id},
                },
                params={
                    "nonce": expected_nonce,
                    "client_id": self.config.client_id,
                    "access_token": token.access_token.get_secret_value(),
                },
            )
            claims.validate(leeway=30)
            id_token_expires_at = float(claims["exp"])
            new_subject = claims["sub"]
            if not isinstance(new_subject, str) or not new_subject:
                raise OidcError("OIDC identity has no subject")
            if previous is not None and new_subject != subject:
                raise OidcError("OIDC refresh changed the authenticated identity")
            subject = new_subject
            id_token = token.id_token
        refresh_token = token.refresh_token
        if refresh_token is None and previous is not None:
            refresh_token = previous.refresh_token
        return OidcTokens(
            issuer=self.config.issuer,
            client_id=self.config.client_id,
            audience=self.config.audience,
            subject=subject,
            access_token=token.access_token,
            refresh_token=refresh_token,
            id_token=id_token,
            expires_at=time.time() + token.expires_in,
            nonce=previous.nonce if previous is not None else nonce,
            id_token_expires_at=id_token_expires_at,
        )
