"""Deterministic proxy-setup responses shared by terminal scenarios."""

from __future__ import annotations

from typing import Any

METHODS = frozenset({"config/proxy/read", "config/proxy/write"})
DESCRIPTIONS = {
    "HTTP_PROXY": "Proxy URL for HTTP requests",
    "HTTPS_PROXY": "Proxy URL for HTTPS requests",
    "ALL_PROXY": "Proxy URL for all requests (fallback)",
    "NO_PROXY": "Comma-separated list of hosts to bypass proxy",
    "SSL_CERT_FILE": "Path to custom SSL certificate file",
    "SSL_CERT_DIR": "Path to directory containing SSL certificates",
}


def handshake(**values: str) -> dict[str, Any]:
    """Answer `config/proxy/read` with `values` and accept every write."""
    return {
        "config/proxy/read": {
            "settings": {
                "values": {key: values.get(key) for key in DESCRIPTIONS},
                "descriptions": DESCRIPTIONS,
            }
        },
        "config/proxy/write": {},
    }


def prefilled() -> dict[str, Any]:
    """The settings Python's pre-populated proxy snapshot starts from."""
    return handshake(
        HTTP_PROXY="http://old-proxy:8080", HTTPS_PROXY="https://old-proxy:8443"
    )
