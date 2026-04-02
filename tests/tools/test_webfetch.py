from __future__ import annotations

import httpx
import pytest
import respx

from tests.mock.utils import collect_result
from vibe.core.tools.base import BaseToolState, ToolError
from vibe.core.tools.builtins.webfetch import WebFetch, WebFetchArgs, WebFetchConfig


@pytest.fixture
def webfetch():
    config = WebFetchConfig()
    return WebFetch(config=config, state=BaseToolState())


@pytest.fixture
def webfetch_small():
    config = WebFetchConfig(max_content_bytes=100)
    return WebFetch(config=config, state=BaseToolState())


@pytest.mark.asyncio
@respx.mock
async def test_bare_domain_gets_https(webfetch):
    respx.get("https://example.com").mock(
        return_value=httpx.Response(
            200, text="ok", headers={"Content-Type": "text/plain"}
        )
    )
    result = await collect_result(webfetch.run(WebFetchArgs(url="example.com")))
    assert result.url == "https://example.com"
    assert result.content == "ok"


@pytest.mark.asyncio
@respx.mock
async def test_http_url_stays_http(webfetch):
    respx.get("http://example.com").mock(
        return_value=httpx.Response(
            200, text="ok", headers={"Content-Type": "text/plain"}
        )
    )
    result = await collect_result(webfetch.run(WebFetchArgs(url="http://example.com")))
    assert result.url == "http://example.com"


@pytest.mark.asyncio
@respx.mock
async def test_https_url_stays_https(webfetch):
    respx.get("https://example.com").mock(
        return_value=httpx.Response(
            200, text="ok", headers={"Content-Type": "text/plain"}
        )
    )
    result = await collect_result(webfetch.run(WebFetchArgs(url="https://example.com")))
    assert result.url == "https://example.com"


@pytest.mark.asyncio
@respx.mock
async def test_protocol_relative_url_normalized(webfetch):
    respx.get("https://example.com").mock(
        return_value=httpx.Response(
            200, text="ok", headers={"Content-Type": "text/plain"}
        )
    )
    result = await collect_result(webfetch.run(WebFetchArgs(url="//example.com")))
    assert result.url == "https://example.com"
    assert result.content == "ok"


@pytest.mark.asyncio
async def test_ftp_scheme_rejected(webfetch):
    with pytest.raises(ToolError, match="Invalid URL scheme: ftp"):
        await collect_result(webfetch.run(WebFetchArgs(url="ftp://example.com")))


@pytest.mark.asyncio
async def test_empty_url_rejected(webfetch):
    with pytest.raises(ToolError, match="URL cannot be empty"):
        await collect_result(webfetch.run(WebFetchArgs(url="   ")))


@pytest.mark.asyncio
@respx.mock
async def test_html_converted_to_markdown(webfetch):
    html = "<html><body><h1>Title</h1><p>Hello world</p></body></html>"
    respx.get("https://example.com").mock(
        return_value=httpx.Response(
            200, text=html, headers={"Content-Type": "text/html; charset=utf-8"}
        )
    )
    result = await collect_result(webfetch.run(WebFetchArgs(url="https://example.com")))
    assert "# Title" in result.content
    assert "Hello world" in result.content


@pytest.mark.asyncio
@respx.mock
async def test_plain_text_unchanged(webfetch):
    respx.get("https://example.com/file.txt").mock(
        return_value=httpx.Response(
            200, text="just text", headers={"Content-Type": "text/plain"}
        )
    )
    result = await collect_result(
        webfetch.run(WebFetchArgs(url="https://example.com/file.txt"))
    )
    assert result.content == "just text"


@pytest.mark.asyncio
@respx.mock
async def test_scripts_stripped_from_markdown(webfetch):
    html = "<html><body><script>alert('xss')</script><style>.x{}</style><p>Clean</p></body></html>"
    respx.get("https://example.com").mock(
        return_value=httpx.Response(
            200, text=html, headers={"Content-Type": "text/html"}
        )
    )
    result = await collect_result(webfetch.run(WebFetchArgs(url="https://example.com")))
    assert "alert" not in result.content
    assert ".x{}" not in result.content
    assert "Clean" in result.content


@pytest.mark.asyncio
@respx.mock
async def test_cloudflare_retry_on_challenge(webfetch):
    route = respx.get("https://example.com")
    route.side_effect = [
        httpx.Response(403, headers={"cf-mitigated": "challenge"}),
        httpx.Response(200, text="success", headers={"Content-Type": "text/plain"}),
    ]
    result = await collect_result(webfetch.run(WebFetchArgs(url="https://example.com")))
    assert result.content == "success"
    assert route.call_count == 2

    second_request = route.calls[1].request
    assert second_request.headers["User-Agent"] == "vibe-cli"


@pytest.mark.asyncio
@respx.mock
async def test_regular_403_not_retried(webfetch):
    route = respx.get("https://example.com").mock(
        return_value=httpx.Response(403, headers={"Content-Type": "text/plain"})
    )
    with pytest.raises(ToolError, match="HTTP error 403"):
        await collect_result(webfetch.run(WebFetchArgs(url="https://example.com")))
    assert route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_truncates_to_max_bytes_with_disclaimer(webfetch_small):
    body = "a" * 200
    respx.get("https://example.com").mock(
        return_value=httpx.Response(
            200, text=body, headers={"Content-Type": "text/plain"}
        )
    )
    result = await collect_result(
        webfetch_small.run(WebFetchArgs(url="https://example.com"))
    )
    assert result.content.startswith("a" * 100)
    assert "[Content truncated due to size limit]" in result.content


@pytest.mark.asyncio
@respx.mock
async def test_truncates_html_with_disclaimer(webfetch_small):
    html = (
        "<html><body><h2>first title</h2>"
        + "x" * 200
        + "<h2>second title</h2></body></html>"
    )
    respx.get("https://example.com").mock(
        return_value=httpx.Response(
            200, text=html, headers={"Content-Type": "text/html"}
        )
    )
    result = await collect_result(
        webfetch_small.run(WebFetchArgs(url="https://example.com"))
    )

    assert "## first title" in result.content
    assert "## second title" not in result.content
    assert "[Content truncated due to size limit]" in result.content


@pytest.mark.asyncio
@respx.mock
async def test_http_404_raises_tool_error(webfetch):
    respx.get("https://example.com").mock(return_value=httpx.Response(404))
    with pytest.raises(ToolError, match="HTTP error 404"):
        await collect_result(webfetch.run(WebFetchArgs(url="https://example.com")))


@pytest.mark.asyncio
@respx.mock
async def test_http_500_raises_tool_error(webfetch):
    respx.get("https://example.com").mock(return_value=httpx.Response(500))
    with pytest.raises(ToolError, match="HTTP error 500"):
        await collect_result(webfetch.run(WebFetchArgs(url="https://example.com")))


@pytest.mark.asyncio
@respx.mock
async def test_timeout_raises_tool_error(webfetch):
    respx.get("https://example.com").mock(side_effect=httpx.ReadTimeout("timed out"))
    with pytest.raises(ToolError, match="Request timed out"):
        await collect_result(webfetch.run(WebFetchArgs(url="https://example.com")))


@pytest.mark.asyncio
@respx.mock
async def test_network_error_raises_tool_error(webfetch):
    respx.get("https://example.com").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    with pytest.raises(ToolError, match="Failed to fetch URL"):
        await collect_result(webfetch.run(WebFetchArgs(url="https://example.com")))


@pytest.mark.asyncio
async def test_negative_timeout_rejected(webfetch):
    with pytest.raises(ToolError, match="Timeout must be a positive number"):
        await collect_result(
            webfetch.run(WebFetchArgs(url="https://example.com", timeout=-1))
        )


@pytest.mark.asyncio
async def test_zero_timeout_rejected(webfetch):
    with pytest.raises(ToolError, match="Timeout must be a positive number"):
        await collect_result(
            webfetch.run(WebFetchArgs(url="https://example.com", timeout=0))
        )


@pytest.mark.asyncio
async def test_over_max_timeout_rejected(webfetch):
    with pytest.raises(ToolError, match="Timeout cannot exceed"):
        await collect_result(
            webfetch.run(WebFetchArgs(url="https://example.com", timeout=999))
        )


def test_get_status_text():
    assert WebFetch.get_status_text() == "Fetching URL"
