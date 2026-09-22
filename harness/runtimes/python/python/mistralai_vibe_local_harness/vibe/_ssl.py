"""Trust anchors for every TLS connection the Runtime opens.

Model APIs, MCP servers and connector gateways resolve trust through this one
function, so a root that works for one of them works for all of them.
"""

import logging
import os
import ssl
from functools import lru_cache

import certifi
import truststore

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def build_ssl_context(*, use_system_trust_store: bool = False) -> ssl.SSLContext:
    if use_system_trust_store:
        return _add_custom_roots(truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT))

    # certifi is layered on top of the platform roots rather than passed as
    # `create_default_context(cafile=...)`: that argument suppresses
    # `load_default_certs()`, which would drop the OS trust store holding
    # corporate CAs. certifi covers the other direction, where an interpreter
    # shipped by uv or PyInstaller has no usable OpenSSL default store.
    context = ssl.create_default_context()
    try:
        context.load_verify_locations(cafile=certifi.where())
    except (OSError, ssl.SSLError):
        logger.warning("Failed to load the certifi CA bundle", exc_info=True)
    return _add_custom_roots(context)


def _add_custom_roots(context: ssl.SSLContext) -> ssl.SSLContext:
    cert_file = os.getenv("SSL_CERT_FILE")
    cert_dir = os.getenv("SSL_CERT_DIR")
    if not cert_file and not cert_dir:
        return context
    try:
        context.load_verify_locations(cafile=cert_file, capath=cert_dir)
    except (OSError, ssl.SSLError):
        logger.warning(
            "Failed to load custom SSL certificates: SSL_CERT_FILE=%s SSL_CERT_DIR=%s",
            cert_file,
            cert_dir,
            exc_info=True,
        )
    return context
