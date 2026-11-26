"""The platform credential of one ``HarnessProcess``'s connection.

A multiplexed server builds one ``HarnessProcess`` per channel, so the
connection's selection (``AppServer.platform_credentials``) is bound here once
the server exists, and every reach of the platform from the process (session
builds, host reads, the picker's fetches) asks it. Until it is bound, and for
a process without a connection, it is the API key.
"""

from __future__ import annotations

from vibe.core.platform_auth import (
    PlatformCredentialFactory,
    PlatformCredentialProvider,
)
from vibe.core.platform_auth._mistral import api_key_credentials


class ConnectionCredentials:
    def __init__(self) -> None:
        self._factory: PlatformCredentialFactory = api_key_credentials

    def bind(self, factory: PlatformCredentialFactory) -> None:
        self._factory = factory

    def __call__(self, api_key_env_var: str) -> PlatformCredentialProvider:
        return self._factory(api_key_env_var)


__all__ = ["ConnectionCredentials"]
