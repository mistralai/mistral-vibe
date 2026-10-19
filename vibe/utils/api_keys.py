from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import os

from vibe.utils.keyring import get_api_key_from_keyring


class ApiKeySource(StrEnum):
    """The places ``resolve_api_key`` looks, in the order it looks."""

    ENVIRONMENT = "environment"
    KEYRING = "keyring"


@dataclass(frozen=True, slots=True)
class ApiKeyOrigin:
    """Where one resolved key came from, and under which name.

    ``env_var`` keys both sources, so it alone cannot say which answered. The
    pair can, and the difference is what a user acts on: an environment
    variable may hold a value they never set, while a keyring key is replaced
    by running setup again.
    """

    source: ApiKeySource
    env_var: str

    def describe(self) -> str:
        if self.source is ApiKeySource.ENVIRONMENT:
            return f"env var {self.env_var}"
        return "the keyring"


def active_api_key(
    *, env_value: str | None, keyring_value: str | None
) -> tuple[ApiKeySource, str] | None:
    """The winning credential and its source, or ``None`` when neither holds one.

    The precedence rule shared by resolution and ``/status``: a truthy
    environment value wins, otherwise the keyring decides. Inputs are
    already-collected values, so callers own when (and whether) the keyring
    is read — resolution touches it only when the environment holds nothing.
    """
    if env_value:
        return ApiKeySource.ENVIRONMENT, env_value
    if keyring_value:
        return ApiKeySource.KEYRING, keyring_value
    return None


def resolve_api_key_with_origin(env_key: str) -> tuple[str, ApiKeyOrigin] | None:
    """The key for ``env_key`` and where it was read from, or ``None``.

    ``active_api_key`` states the env-wins rule, so a key and its reported
    origin agree. The keyring read goes through its process-wide cache, so
    asking again after a failed call costs nothing — and happens only when
    the environment holds no value.
    """
    if not env_key:
        return None
    env_value = os.environ.get(env_key)
    keyring_value = None if env_value else get_api_key_from_keyring(env_key)
    resolved = active_api_key(env_value=env_value, keyring_value=keyring_value)
    if resolved is None:
        return None
    source, token = resolved
    return token, ApiKeyOrigin(source, env_key)


def resolve_api_key(env_key: str) -> str | None:
    resolved = resolve_api_key_with_origin(env_key)
    return resolved[0] if resolved else None
