from __future__ import annotations

import httpx
import pytest

from vibe.core.utils.http import (
    VibeAsyncHTTPClient,
    _EnvProxyTransport,
    _normalize_proxy_url,
    _should_bypass_proxy,
)

PROXY_ENV_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in PROXY_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.mark.asyncio
async def test_vibe_async_http_client_accepts_ipv6_cidr_no_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:8080")
    monkeypatch.setenv("NO_PROXY", "fc00::/7")

    client = VibeAsyncHTTPClient()
    try:
        assert isinstance(client, VibeAsyncHTTPClient)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_vibe_async_http_client_builds_proxy_transport_from_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.utils.http.getproxies",
        lambda: {
            "http": "proxy.local:8080",
            "https": "https://secure-proxy.local:8443",
            "no": " fc00::/7, .asml.com, ",
        },
    )

    client = VibeAsyncHTTPClient()
    try:
        assert client._trust_env is False
        assert isinstance(client._transport, _EnvProxyTransport)
        assert client._transport._no_proxy == ("fc00::/7", ".asml.com")
        assert set(client._transport._proxies) == {"http", "https"}
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_vibe_async_http_client_keeps_explicit_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "vibe.core.utils.http.getproxies",
        lambda: {"http": "proxy.local:8080", "no": "*"},
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200))

    client = VibeAsyncHTTPClient(transport=transport)
    try:
        assert client._trust_env is False
        assert client._transport is transport
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        pytest.param(None, None, id="missing"),
        pytest.param("", None, id="empty"),
        pytest.param(
            " proxy.local:8080 ", "http://proxy.local:8080", id="adds-http-scheme"
        ),
        pytest.param(
            "https://secure-proxy.local:8443",
            "https://secure-proxy.local:8443",
            id="keeps-existing-scheme",
        ),
    ],
)
def test_normalize_proxy_url(value: str | None, expected: str | None) -> None:
    assert _normalize_proxy_url(value) == expected


@pytest.mark.parametrize(
    ("url", "rules", "expected"),
    [
        pytest.param(
            "https://[fc00::1]/", ("fc00::/7",), True, id="ipv6-cidr-matches-ip-literal"
        ),
        pytest.param(
            "https://api.mistral.ai/",
            ("fc00::/7",),
            False,
            id="cidr-does-not-resolve-hostnames",
        ),
        pytest.param(
            "http://127.0.0.1/",
            ("127.0.0.0/8",),
            True,
            id="ipv4-cidr-matches-ip-literal",
        ),
        pytest.param("http://127.0.0.1/", ("127.0.0.1",), True, id="ipv4-literal"),
        pytest.param(
            "https://foo.127.0.0.1/",
            ("127.0.0.1",),
            False,
            id="ipv4-literal-does-not-suffix-match-hostname",
        ),
        pytest.param("http://[::1]/", ("::1",), True, id="ipv6-literal"),
        pytest.param("https://api.mistral.ai/", ("*",), True, id="wildcard"),
        pytest.param("https://asml.com/", ("asml.com",), True, id="bare-domain"),
        pytest.param(
            "https://portal.asml.com/", ("asml.com",), True, id="bare-domain-subdomain"
        ),
        pytest.param(
            "https://asml.com/", (".asml.com",), False, id="leading-dot-excludes-apex"
        ),
        pytest.param(
            "https://portal.asml.com/", (".asml.com",), True, id="leading-dot-subdomain"
        ),
        pytest.param(
            "http://localhost:8000/", ("localhost:8000",), True, id="host-port-match"
        ),
        pytest.param(
            "http://127.0.0.1:8000/",
            ("127.0.0.1:8000",),
            True,
            id="ipv4-literal-port-match",
        ),
        pytest.param(
            "http://foo.127.0.0.1:8000/",
            ("127.0.0.1:8000",),
            False,
            id="ipv4-literal-port-does-not-suffix-match-hostname",
        ),
        pytest.param(
            "http://localhost:9000/",
            ("localhost:8000",),
            False,
            id="host-port-mismatch",
        ),
        pytest.param(
            "http://example.com/",
            ("https://example.com",),
            False,
            id="scheme-specific-mismatch",
        ),
        pytest.param(
            "https://example.com/",
            ("https://example.com",),
            True,
            id="scheme-specific-match",
        ),
        pytest.param(
            "https://example.com/",
            ("https://example.com:443",),
            True,
            id="scheme-port-default-match",
        ),
        pytest.param(
            "https://example.com:8443/",
            ("https://example.com:443",),
            False,
            id="scheme-port-mismatch",
        ),
        pytest.param(
            "http://[::1]:8000/", ("[::1]:8000",), True, id="bracketed-ipv6-port-match"
        ),
    ],
)
def test_should_bypass_proxy_documents_supported_no_proxy_rules(
    url: str, rules: tuple[str, ...], expected: bool
) -> None:
    assert _should_bypass_proxy(httpx.URL(url), rules) is expected
