from __future__ import annotations

import re

from vibe import __version__
from vibe.core.types import Backend


def get_user_agent(backend: Backend | None) -> str:
    user_agent = f"Mistral-Vibe/{__version__}"
    if backend == Backend.MISTRAL:
        mistral_sdk_prefix = "mistral-client-python/"
        user_agent = f"{mistral_sdk_prefix}{user_agent}"
    return user_agent


def get_server_url_from_api_base(api_base: str) -> str | None:
    match = re.match(r"(https?://.+)(/v\d+.*)", api_base)
    return match.group(1) if match else None
