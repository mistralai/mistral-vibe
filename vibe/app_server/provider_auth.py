"""Redacted public view of the active provider's authentication state.

A read-only snapshot for ``/status``: it carries no credential material and no
validation state — only which sources exist, which one is in use, and which
could not be inspected.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from vibe.app_server._model import ProtocolModel

__all__ = [
    "ProviderAuthKind",
    "ProviderAuthView",
    "ProviderCredentialSourceKind",
    "ProviderCredentialSourceState",
    "ProviderCredentialSourceView",
]


class ProviderAuthKind(StrEnum):
    """How the active provider authenticates model requests."""

    API_KEY = "api_key"
    NONE = "none"
    GOOGLE_ADC = "google_adc"


class ProviderCredentialSourceKind(StrEnum):
    """The API-key sources Vibe resolves credentials from.

    Precedence is decided at use, not by member order.
    """

    PROCESS_ENV = "process_env"
    VIBE_HOME_ENV_FILE = "vibe_home_env_file"
    OS_KEYRING = "os_keyring"


class ProviderCredentialSourceState(StrEnum):
    """What one source's inspection says about it.

    One field instead of two flags, so the nonsense combinations — a source
    that is both in use and uninspectable — cannot be expressed.
    """

    IN_USE = "in_use"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ProviderCredentialSourceView(ProtocolModel):
    kind: ProviderCredentialSourceKind
    # The source whose credential the next model call would send; a source
    # that holds a credential nothing uses; a source that could not be
    # inspected, so whether it holds one is unknown. An in-use source never
    # reports UNAVAILABLE: its credential was loaded at startup and serves
    # regardless of whether its file can be re-read.
    state: ProviderCredentialSourceState = ProviderCredentialSourceState.AVAILABLE


class ProviderAuthView(ProtocolModel):
    model_display_name: str
    provider_name: str
    # The sanitized API base (scheme, host, optional port, path), or ``None``
    # when it is invalid or cannot be displayed safely.
    api_base: str | None = None
    auth_kind: ProviderAuthKind
    # The configured environment-variable name for API-key providers.
    api_key_env_var: str | None = None
    # The resolved path of the ``$VIBE_HOME/.env`` file this snapshot
    # inspected; ``None`` for kinds that inspect no sources. The client
    # renders it as the source label, so the label can never diverge from
    # the file actually probed.
    env_file_path: str | None = None
    # The active source first, the remaining available or unavailable sources in
    # current precedence order. Empty when every applicable source was checked
    # and none contains a credential.
    sources: list[ProviderCredentialSourceView] = Field(default_factory=list)
