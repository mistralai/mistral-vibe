from __future__ import annotations

import os
import time

from joserfc import jwt
from joserfc.jwk import RSAKey
from pydantic import SecretStr
import pytest

from vibe.setup.auth._oidc_config import OidcConfig
from vibe.setup.auth._oidc_credentials import OidcCredentialStore, OidcTokens


@pytest.fixture
def tokens(monkeypatch: pytest.MonkeyPatch) -> OidcTokens:
    for name in list(os.environ):
        if name.startswith("VIBE_CLI_OIDC_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("VIBE_CLI_OIDC_ENABLED", "true")
    monkeypatch.setenv("VIBE_CLI_OIDC_ORGANIZATION_ID", "test-org")
    config = OidcConfig.from_env()
    # The platform consumes ID tokens, not access tokens:
    # albe/albe/services/auth/services/oidc_id_token.py:get_external_identity.
    id_token = jwt.encode(
        {"alg": "RS256"}, {"exp": time.time() + 3600}, RSAKey.generate_key(2048)
    )
    value = OidcTokens(
        issuer=config.issuer,
        client_id=config.client_id,
        audience=config.audience,
        subject="user",
        access_token=SecretStr("access-secret"),
        refresh_token=SecretStr("refresh-secret"),
        id_token=SecretStr(id_token),
        id_token_expires_at=time.time() + 3600,
        expires_at=time.time() + 3600,
    )
    monkeypatch.setattr(OidcCredentialStore, "_load", lambda self: value)
    return value
