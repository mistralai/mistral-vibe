from __future__ import annotations

import functools
import ipaddress
import os
import re
import ssl
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse
from urllib.request import getproxies

import certifi
import httpx
import truststore

from vibe import __version__

if TYPE_CHECKING:
    from mistralai.client import Mistral
    from mistralai.client.httpclient import AsyncHttpClient

_use_system_trust_store = False
# Set by `pin_ssl_context`: the trust store no longer follows each config load.
_pinned = False


def configure_ssl_context(*, enable_system_trust_store: bool) -> None:
    """Use the system trust store, or certifi's, for the process's TLS.

    Does nothing once :func:`pin_ssl_context` has fixed it.
    """
    global _use_system_trust_store
    if _pinned or _use_system_trust_store == enable_system_trust_store:
        return
    _use_system_trust_store = enable_system_trust_store
    build_ssl_context.cache_clear()


def pin_ssl_context(*, enable_system_trust_store: bool) -> None:
    """Fix the process's trust store, whatever configs it loads later.

    For a process serving many configs at once, where the store, being the
    process's, cannot follow each.
    """
    global _pinned
    _pinned = False
    configure_ssl_context(enable_system_trust_store=enable_system_trust_store)
    _pinned = True


@functools.lru_cache(maxsize=1)
def build_ssl_context() -> ssl.SSLContext:
    if _use_system_trust_store:
        ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    else:
        ctx = ssl.create_default_context(cafile=certifi.where())
        # Match the Python 3.13+ defaults on 3.12: strict RFC 5280 checks, and
        # a trusted intermediate may anchor a chain.
        ctx.verify_flags |= ssl.VERIFY_X509_STRICT | ssl.VERIFY_X509_PARTIAL_CHAIN

    # Custom certs are additive so private-CA users don't lose public roots.
    ssl_cert_file = os.getenv("SSL_CERT_FILE")
    ssl_cert_dir = os.getenv("SSL_CERT_DIR")
    if ssl_cert_file or ssl_cert_dir:
        try:
            ctx.load_verify_locations(cafile=ssl_cert_file, capath=ssl_cert_dir)
        except (OSError, ssl.SSLError):
            from vibe.observability.logging import logger

            logger.warning(
                "Failed to load custom SSL certificates: SSL_CERT_FILE=%s SSL_CERT_DIR=%s",
                ssl_cert_file,
                ssl_cert_dir,
            )
        else:
            if not _use_system_trust_store:
                # The Certifi context validates chains with strict RFC 5280
                # checks (VERIFY_X509_STRICT); the truststore context does
                # not, so only warn on this path.
                from vibe.observability.logging import logger

                logger.warning(
                    "Loaded custom SSL certificates (SSL_CERT_FILE=%s SSL_CERT_DIR=%s); "
                    "certificate chains are validated with strict RFC 5280 checks, "
                    "so a private CA missing SKID/AKID or KeyUsage extensions will "
                    "be rejected",
                    ssl_cert_file,
                    ssl_cert_dir,
                )
    return ctx


class _EnvProxyTransport(httpx.AsyncBaseTransport):
    def __init__(
        self, proxies: dict[str, str], no_proxy: tuple[str, ...], **kwargs: Any
    ) -> None:
        self._no_proxy = no_proxy
        self._direct = httpx.AsyncHTTPTransport(**kwargs)
        self._proxies = {
            scheme: httpx.AsyncHTTPTransport(proxy=proxy, **kwargs)
            for scheme, proxy in proxies.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if _should_bypass_proxy(request.url, self._no_proxy):
            return await self._direct.handle_async_request(request)

        transport = self._proxies.get(request.url.scheme) or self._proxies.get("all")
        if transport is None:
            transport = self._direct
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        transports = [self._direct, *self._proxies.values()]
        for transport in dict.fromkeys(transports):
            await transport.aclose()


class VibeAsyncHTTPClient(httpx.AsyncClient):
    """HTTPX client that works around HTTPX's CIDR NO_PROXY limitation."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs["trust_env"] = False
        if (
            kwargs.get("transport") is None
            and kwargs.get("proxy") is None
            and kwargs.get("mounts") is None
        ):
            proxy_info = getproxies()
            proxies = {
                scheme: proxy_url
                for scheme in ("http", "https", "all")
                if (proxy_url := _normalize_proxy_url(proxy_info.get(scheme)))
            }
            if proxies:
                transport_kwargs: dict[str, Any] = {"trust_env": False}
                for key in ("verify", "cert", "http1", "http2", "limits"):
                    if key in kwargs:
                        transport_kwargs[key] = kwargs.pop(key)
                kwargs["transport"] = _EnvProxyTransport(
                    proxies,
                    tuple(
                        part.strip()
                        for part in proxy_info.get("no", "").split(",")
                        if part.strip()
                    ),
                    **transport_kwargs,
                )
        super().__init__(**kwargs)


def _normalize_proxy_url(value: str | None) -> str | None:
    if value is None or not (value := value.strip()):
        return None
    if "://" in value:
        return value
    return f"http://{value}"


def _should_bypass_proxy(url: httpx.URL, no_proxy: tuple[str, ...]) -> bool:
    host = url.host
    if host is None:
        return False

    host = host.strip("[]").lower()
    port = url.port or {"http": 80, "https": 443}.get(url.scheme)
    for rule in no_proxy:
        if _no_proxy_rule_matches(rule.lower(), host, url.scheme, port):
            return True
    return False


def _no_proxy_rule_matches(rule: str, host: str, scheme: str, port: int | None) -> bool:
    if rule == "*":
        return True

    if (matches := _ip_network_rule_matches(rule, host)) is not None:
        return matches

    parsed_rule = _parse_no_proxy_host_rule(rule, scheme)
    if parsed_rule is None:
        return False
    rule, rule_port = parsed_rule

    if rule_port is not None and rule_port != port:
        return False

    if (matches := _ip_literal_rule_matches(rule, host)) is not None:
        return matches

    return _host_rule_matches(rule, host)


def _ip_network_rule_matches(rule: str, host: str) -> bool | None:
    try:
        network = ipaddress.ip_network(rule.strip("[]"), strict=False)
    except ValueError:
        return None

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip in network


def _ip_literal_rule_matches(rule: str, host: str) -> bool | None:
    try:
        rule_ip = ipaddress.ip_address(rule)
    except ValueError:
        return None

    try:
        host_ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return host_ip == rule_ip


def _host_rule_matches(rule: str, host: str) -> bool:
    if rule.startswith("."):
        return host.endswith(rule)
    return host == rule or host.endswith(f".{rule}")


def _parse_no_proxy_host_rule(rule: str, scheme: str) -> tuple[str, int | None] | None:
    port: int | None = None
    if "://" in rule:
        parsed = urlparse(rule)
        if parsed.scheme and parsed.scheme != scheme:
            return None
        try:
            port = parsed.port
        except ValueError:
            return None
        rule = parsed.hostname or ""
    elif rule.startswith("["):
        parsed = urlparse(f"//{rule}")
        try:
            port = parsed.port
        except ValueError:
            return None
        rule = parsed.hostname or rule
    elif rule.count(":") == 1:
        rule_host, _, raw_rule_port = rule.partition(":")
        if raw_rule_port.isdigit():
            rule = rule_host
            port = int(raw_rule_port)

    if not rule:
        return None
    return rule, port


def get_user_agent(backend: str | None) -> str:
    user_agent = f"Mistral-Vibe/{__version__}"
    if backend == "mistral":
        mistral_sdk_prefix = "mistral-client-python/"
        user_agent = f"{mistral_sdk_prefix}{user_agent}"
    return user_agent


def get_server_url_from_api_base(api_base: str) -> str | None:
    match = re.match(r"(https?://.+)(/v\d+.*)", api_base)
    return match.group(1) if match else None


def mistral_sdk_client(client: httpx.AsyncClient) -> AsyncHttpClient:
    # TODO(httpx2 migration): mistralai 3 is typed against httpx2. Until Vibe
    # moves to httpx2, the SDK runs on our httpx client, which it drives the
    # same way, and raises httpx errors that our error handling expects.
    return cast("AsyncHttpClient", client)


def disable_sdk_env_telemetry(client: Mistral) -> None:
    # TODO(httpx2 migration): the SDK's own tracing, on when MISTRAL_SDK_TELEMETRY
    # is set, wraps our httpx response in an httpx2 one and breaks every stream.
    # Vibe's own tracing (configure_telemetry) is unaffected by this setting.
    client.sdk_configuration.telemetry = False  # pyright: ignore[reportAttributeAccessIssue]


__all__ = [
    "VibeAsyncHTTPClient",
    "build_ssl_context",
    "configure_ssl_context",
    "disable_sdk_env_telemetry",
    "get_server_url_from_api_base",
    "get_user_agent",
    "mistral_sdk_client",
    "pin_ssl_context",
]
