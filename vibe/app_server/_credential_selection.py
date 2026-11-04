"""Which platform credential one app-server connection authenticates with.

One provider per connection, decided by who connected:

- a client that resolves its own tokens (Desktop) uses them;
- a CLI client (``cli``, ``programmatic``, ``acp``) uses the CLI's own OIDC
  session when the flag is on;
- otherwise the API key, as before.

A Desktop connection never reads the CLI keyring, even when the CLI flag
leaks into its environment.
"""

from __future__ import annotations

from vibe.app_server._connection_protocol import ClientInfo
from vibe.core.platform_auth import (
    ApiKeyPlatformCredentialProvider,
    PlatformCredentialProvider,
)
from vibe.setup.auth._oidc_tenant import OidcTenantResolver
from vibe.utils.oidc_marker import oidc_enabled

_CLI_ENTRYPOINTS = frozenset({"cli", "programmatic", "acp"})
MISTRAL_API_KEY_ENV = "MISTRAL_API_KEY"


def uses_cli_oidc(client: ClientInfo | None, api_key_env_var: str) -> bool:
    return (
        oidc_enabled()
        and (api_key_env_var or MISTRAL_API_KEY_ENV) == MISTRAL_API_KEY_ENV
        and _is_cli(client)
    )


def select_platform_credentials(
    *,
    client: ClientInfo | None,
    connection: PlatformCredentialProvider | None,
    tenants: OidcTenantResolver,
    api_key_env_var: str,
) -> PlatformCredentialProvider:
    if connection is not None:
        return connection
    if uses_cli_oidc(client, api_key_env_var):
        from vibe.setup.auth._cli_oidc_platform_credentials import (
            CliOidcPlatformCredentialProvider,
        )

        return CliOidcPlatformCredentialProvider(tenants)
    return ApiKeyPlatformCredentialProvider(api_key_env_var, cli_oidc=_is_cli(client))


def _is_cli(client: ClientInfo | None) -> bool:
    return client is not None and client.entrypoint in _CLI_ENTRYPOINTS


class ConnectionCredentialSelection:
    """One connection's ``PlatformCredentialFactory``.

    The CLI OIDC provider is built once per connection and kept, so the tokens
    it saw rejected stay withheld for the connection's lifetime instead of one
    call. An API key is still read afresh on every call, as before: a key the
    platform refused once is tried again on a later request. The CLI flag is
    read on every call.
    """

    def __init__(
        self,
        *,
        client: ClientInfo | None,
        connection: PlatformCredentialProvider | None,
        tenants: OidcTenantResolver,
    ) -> None:
        self._client = client
        self._connection = connection
        self._tenants = tenants
        self._cli_oidc: PlatformCredentialProvider | None = None

    def __call__(self, api_key_env_var: str) -> PlatformCredentialProvider:
        if self._connection is None and uses_cli_oidc(self._client, api_key_env_var):
            if self._cli_oidc is None:
                self._cli_oidc = self._select(api_key_env_var)
            return self._cli_oidc
        return self._select(api_key_env_var)

    def _select(self, api_key_env_var: str) -> PlatformCredentialProvider:
        return select_platform_credentials(
            client=self._client,
            connection=self._connection,
            tenants=self._tenants,
            api_key_env_var=api_key_env_var,
        )


def cli_process_credentials(api_key_env_var: str) -> PlatformCredentialProvider:
    """The selection for a CLI process's own clients (``vibe-acp`` audio),
    which run outside any app-server connection. Each client discovers its
    tenant once, with its own resolver.
    """
    return select_platform_credentials(
        client=ClientInfo(name="vibe_cli_process", version="", entrypoint="cli"),
        connection=None,
        tenants=OidcTenantResolver(),
        api_key_env_var=api_key_env_var,
    )


__all__ = [
    "MISTRAL_API_KEY_ENV",
    "ConnectionCredentialSelection",
    "cli_process_credentials",
    "select_platform_credentials",
    "uses_cli_oidc",
]
