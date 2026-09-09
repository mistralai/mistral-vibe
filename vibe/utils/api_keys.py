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


def resolve_api_key_with_origin(env_key: str) -> tuple[str, ApiKeyOrigin] | None:
    """The key for ``env_key`` and where it was read from, or ``None``.

    The one place the precedence lives, so a key and its reported origin can
    never disagree. The keyring read goes through its process-wide cache, so
    asking again after a failed call costs nothing.
    """
    if not env_key:
        return None
    if token := os.environ.get(env_key):
        return token, ApiKeyOrigin(ApiKeySource.ENVIRONMENT, env_key)
    if token := get_api_key_from_keyring(env_key):
        return token, ApiKeyOrigin(ApiKeySource.KEYRING, env_key)
    return None


def resolve_api_key(env_key: str) -> str | None:
    resolved = resolve_api_key_with_origin(env_key)
    return resolved[0] if resolved else None
