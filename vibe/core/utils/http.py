from __future__ import annotations

import functools
import os
import re
import ssl

import certifi

from vibe import __version__
from vibe.core.types import Backend


@functools.lru_cache(maxsize=1)
def build_ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=certifi.where())

    # Custom certs are additive so private-CA users don't lose public roots.
    ssl_cert_file = os.getenv("SSL_CERT_FILE")
    ssl_cert_dir = os.getenv("SSL_CERT_DIR")
    if ssl_cert_file or ssl_cert_dir:
        try:
            ctx.load_verify_locations(cafile=ssl_cert_file, capath=ssl_cert_dir)
        except (OSError, ssl.SSLError):
            from vibe.core.logger import logger

            logger.warning(
                "Failed to load custom SSL certificates: SSL_CERT_FILE=%s SSL_CERT_DIR=%s",
                ssl_cert_file,
                ssl_cert_dir,
            )
    return ctx


def get_user_agent(backend: Backend | None) -> str:
    user_agent = f"Mistral-Vibe/{__version__}"
    if backend == Backend.MISTRAL:
        mistral_sdk_prefix = "mistral-client-python/"
        user_agent = f"{mistral_sdk_prefix}{user_agent}"
    return user_agent


def get_server_url_from_api_base(api_base: str) -> str | None:
    match = re.match(r"(https?://.+)(/v\d+.*)", api_base)
    return match.group(1) if match else None
