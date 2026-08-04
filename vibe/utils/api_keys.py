from __future__ import annotations

import os

from vibe.utils.keyring import get_api_key_from_keyring


def resolve_api_key(env_key: str) -> str | None:
    if not env_key:
        return None
    return os.environ.get(env_key) or get_api_key_from_keyring(env_key)
