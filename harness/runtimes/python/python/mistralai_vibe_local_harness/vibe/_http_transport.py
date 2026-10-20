# ruff: noqa: I002 -- new modules here do without postponed annotations.
"""Transports for the Runtime's outbound HTTP connections.

HTTPX applies the proxy settings of the environment only to the transports it
builds itself. A client that needs its own transport, for example to set socket
options, gets the same routing from ``env_proxy_transport``. Its ``NO_PROXY``
rules also accept CIDR ranges and ports.
"""

from collections.abc import Sequence
from functools import cache
import ipaddress
import socket
import ssl
from typing import TypedDict, Unpack
from urllib.parse import urlparse
from urllib.request import getproxies

import httpx

type SocketOption = tuple[int, int, int]


class TransportOptions(TypedDict, total=False):
    verify: ssl.SSLContext
    limits: httpx.Limits
    socket_options: Sequence[SocketOption]


def env_proxy_transport(
    **options: Unpack[TransportOptions],
) -> httpx.AsyncBaseTransport:
    """A transport that sends each request through the proxy the environment sets."""
    proxy_info = getproxies()
    proxies = {
        scheme: proxy_url
        for scheme in ("http", "https", "all")
        if (proxy_url := _normalize_proxy_url(proxy_info.get(scheme)))
    }
    if not proxies:
        return httpx.AsyncHTTPTransport(trust_env=False, **options)
    no_proxy = tuple(
        part.strip() for part in proxy_info.get("no", "").split(",") if part.strip()
    )
    return _EnvProxyTransport(proxies, no_proxy, **options)


@cache
def keepalive_socket_options() -> tuple[SocketOption, ...]:
    """TCP keepalive options that close a connection whose peer stopped answering.

    After a network change or a system sleep, a connection can stop receiving
    packets without a reset, and a read on it waits for its full timeout. With
    these options the operating system probes the connection after 30 seconds
    without traffic, and closes it after 3 probes 10 seconds apart that get no
    answer. A peer that is alive answers the probes, however long its response
    takes.

    Platforms name these options differently and can reject one of them. HTTPX
    sets the options after it connects, and a rejected option fails the
    connection, so only the options that a new socket accepts are returned.
    """
    candidates: list[SocketOption] = [(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)]
    for name, value in (
        ("TCP_KEEPIDLE", _KEEPALIVE_IDLE_S),
        ("TCP_KEEPALIVE", _KEEPALIVE_IDLE_S),
        ("TCP_KEEPINTVL", _KEEPALIVE_INTERVAL_S),
        ("TCP_KEEPCNT", _KEEPALIVE_PROBES),
    ):
        if (option := getattr(socket, name, None)) is not None:
            candidates.append((socket.IPPROTO_TCP, option, value))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        return tuple(option for option in candidates if _accepts(probe, option))


_KEEPALIVE_IDLE_S = 30
_KEEPALIVE_INTERVAL_S = 10
_KEEPALIVE_PROBES = 3


def _accepts(probe: socket.socket, option: SocketOption) -> bool:
    try:
        probe.setsockopt(*option)
    except OSError:
        return False
    return True


class _EnvProxyTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        proxies: dict[str, str],
        no_proxy: tuple[str, ...],
        **options: Unpack[TransportOptions],
    ) -> None:
        self._no_proxy = no_proxy
        self._direct = httpx.AsyncHTTPTransport(trust_env=False, **options)
        self._proxies = {
            scheme: httpx.AsyncHTTPTransport(proxy=proxy, trust_env=False, **options)
            for scheme, proxy in proxies.items()
        }

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if _should_bypass_proxy(request.url, self._no_proxy):
            return await self._direct.handle_async_request(request)
        transport = self._proxies.get(request.url.scheme) or self._proxies.get("all")
        return await (transport or self._direct).handle_async_request(request)

    async def aclose(self) -> None:
        transports = [self._direct, *self._proxies.values()]
        for transport in dict.fromkeys(transports):
            await transport.aclose()


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
    return any(
        _no_proxy_rule_matches(rule.lower(), host, url.scheme, port)
        for rule in no_proxy
    )


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


__all__ = [
    "SocketOption",
    "TransportOptions",
    "env_proxy_transport",
    "keepalive_socket_options",
]
