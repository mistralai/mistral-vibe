from __future__ import annotations

from dataclasses import dataclass
import os
from urllib.parse import urlsplit

from vibe.utils.oidc_marker import oidc_enabled


@dataclass(frozen=True, slots=True)
class _CliRegistration:
    client_id: str
    audience: str
    # First-party web origins that also accept the CLI ID token.
    web_origins: tuple[str, ...]


# Dedicated CLI registrations: sre-infra PR #10140, VIBE-5365.
_CLI_REGISTRATIONS = {
    "https://auth.globalaegis.net": _CliRegistration(
        client_id="3484b11e-2029-4a52-b289-dc826fc03c00",
        audience="https://api.globalaegis.net",
        web_origins=("https://chat.globalaegis.net", "https://console.globalaegis.net"),
    ),
    "https://auth.mistral.ai": _CliRegistration(
        client_id="829da7e7-628f-4795-829a-8cca609c76d4",
        audience="https://api.mistral.ai",
        web_origins=("https://chat.mistral.ai", "https://console.mistral.ai"),
    ),
}
_DEFAULT_ISSUER = "https://auth.mistral.ai"


class OidcError(Exception):
    """A safe-to-display error from the isolated CLI experiment."""


def require_oidc_enabled() -> None:
    if not oidc_enabled():
        raise OidcError("CLI OIDC experiment is disabled")


def validate_oidc_url(value: str, *, loopback: bool = False) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError:
        raise OidcError("Invalid OIDC URL port") from None
    local = parsed.hostname in {"127.0.0.1", "::1"}
    invalid_origin = (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    )
    invalid_scheme = parsed.scheme != "https" and not (
        local and parsed.scheme == "http"
    )
    if invalid_origin or invalid_scheme or parsed.query or parsed.fragment:
        raise OidcError("OIDC URLs must use HTTPS (HTTP is allowed only on loopback)")
    if loopback and (not local or parsed.scheme != "http" or not port):
        raise OidcError(
            "OIDC redirect must use HTTP on a loopback IP with a fixed port"
        )
    return value


@dataclass(frozen=True, slots=True)
class OidcConfig:
    issuer: str
    client_id: str
    audience: str
    redirect_uri: str
    scope: str = "openid offline_access email profile"

    @property
    def web_origins(self) -> tuple[str, ...]:
        registration = _CLI_REGISTRATIONS.get(self.issuer)
        return registration.web_origins if registration is not None else ()

    @classmethod
    def from_env(cls) -> OidcConfig:
        require_oidc_enabled()
        issuer = os.environ.get("VIBE_CLI_OIDC_ISSUER", _DEFAULT_ISSUER)
        registration = _CLI_REGISTRATIONS.get(issuer)
        client_id = os.environ.get(
            "VIBE_CLI_OIDC_CLIENT_ID",
            registration.client_id if registration is not None else "",
        )
        if not issuer or not client_id:
            raise OidcError("Set VIBE_CLI_OIDC_CLIENT_ID for the configured issuer")
        redirect = os.environ.get(
            "VIBE_CLI_OIDC_REDIRECT_URI", "http://127.0.0.1:8766/callback"
        )
        audience = os.environ.get(
            "VIBE_CLI_OIDC_AUDIENCE",
            registration.audience if registration is not None else "",
        )
        validate_oidc_url(issuer)
        validate_oidc_url(redirect, loopback=True)
        if audience:
            # The audience origin receives ID tokens: never over cleartext.
            validate_oidc_url(audience)
        scope = os.environ.get(
            "VIBE_CLI_OIDC_SCOPE", "openid offline_access email profile"
        )
        if "openid" not in scope.split():
            raise OidcError("OIDC scope must include openid")
        return cls(
            issuer=issuer,
            client_id=client_id,
            audience=audience,
            redirect_uri=redirect,
            scope=scope,
        )
