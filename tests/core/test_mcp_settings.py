from __future__ import annotations

from pathlib import Path
import tomllib

import pytest

from tests.conftest import build_test_vibe_config
from vibe.core.config import MCPHttp, MCPOAuth, MCPStreamableHttp, VibeConfig
from vibe.core.tools.mcp_settings import (
    MCPServerAddError,
    parse_mcp_add_transport,
    persist_oauth_mcp_server,
)


def _persisted_servers(config_dir: Path) -> list[dict]:
    with (config_dir / "config.toml").open("rb") as file:
        return tomllib.load(file).get("mcp_servers", [])


@pytest.mark.parametrize(
    ("url", "expected_url"),
    [
        ("https://mcp.example.com/mcp", "https://mcp.example.com/mcp"),
        ("http://localhost:8000/mcp", "http://localhost:8000/mcp"),
        ("http://127.0.0.1:8000/mcp", "http://127.0.0.1:8000/mcp"),
        ("https://example.com:443/mcp?tenant=a", "https://example.com/mcp?tenant=a"),
    ],
)
def test_add_oauth_mcp_server_accepts_supported_urls(
    url: str, expected_url: str, config_dir: Path
) -> None:
    result = persist_oauth_mcp_server(build_test_vibe_config(), url=url)

    assert result.created is True
    assert result.url == expected_url
    assert _persisted_servers(config_dir)[0]["url"] == expected_url


@pytest.mark.parametrize(
    "url",
    [
        "mcp.example.com/mcp",
        "ftp://mcp.example.com/mcp",
        "https:///mcp",
        "http://mcp.example.com/mcp",
        "https://mcp.example.com/mcp#section",
    ],
)
def test_add_oauth_mcp_server_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(MCPServerAddError):
        persist_oauth_mcp_server(build_test_vibe_config(), url=url)


@pytest.mark.parametrize(
    ("url", "expected_name"),
    [
        ("https://mcp.linear.app/mcp", "linear"),
        ("https://example.com/mcp", "example"),
        ("https://mcp.example.com/notion/mcp", "example"),
        ("https://mcp.localhost/notion/mcp", "notion"),
    ],
)
def test_add_oauth_mcp_server_generates_alias(url: str, expected_name: str) -> None:
    result = persist_oauth_mcp_server(build_test_vibe_config(), url=url)

    assert result.name == expected_name


def test_add_oauth_mcp_server_suffixes_generated_alias_collision(
    config_dir: Path,
) -> None:
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStreamableHttp(
                name="linear",
                transport="streamable-http",
                url="https://other.example.com/mcp",
            )
        ]
    )

    result = persist_oauth_mcp_server(config, url="https://mcp.linear.app/mcp")

    assert result.name == "linear_2"
    assert _persisted_servers(config_dir)[0]["name"] == "linear_2"


def test_add_oauth_mcp_server_is_idempotent_for_existing_url(config_dir: Path) -> None:
    first = persist_oauth_mcp_server(
        build_test_vibe_config(), url="https://mcp.linear.app/mcp"
    )
    second = persist_oauth_mcp_server(
        VibeConfig.load(), url="https://mcp.linear.app:443/mcp/"
    )

    assert first.created is True
    assert second.created is False
    assert second.name == "linear"
    assert len(_persisted_servers(config_dir)) == 1


def test_add_oauth_mcp_server_rejects_active_static_url_match() -> None:
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStreamableHttp(
                name="docs",
                transport="streamable-http",
                url="https://mcp.example.com/mcp",
            )
        ]
    )

    with pytest.raises(MCPServerAddError, match="`docs` with static auth"):
        persist_oauth_mcp_server(config, url="https://mcp.example.com/mcp")


def test_add_oauth_mcp_server_rejects_persisted_static_url_match() -> None:
    VibeConfig.save_updates({
        "mcp_servers": [
            {
                "name": "docs",
                "transport": "streamable-http",
                "url": "https://mcp.example.com/mcp",
            }
        ]
    })

    with pytest.raises(MCPServerAddError, match="`docs` with static auth"):
        persist_oauth_mcp_server(
            build_test_vibe_config(), url="https://mcp.example.com/mcp"
        )


def test_add_oauth_mcp_server_rejects_existing_url_with_different_name() -> None:
    persist_oauth_mcp_server(build_test_vibe_config(), url="https://mcp.linear.app/mcp")

    with pytest.raises(MCPServerAddError, match="already configured as `linear`"):
        persist_oauth_mcp_server(
            VibeConfig.load(), url="https://mcp.linear.app/mcp", name="other"
        )


def test_add_oauth_mcp_server_rejects_explicit_alias_collision() -> None:
    config = build_test_vibe_config(
        mcp_servers=[
            MCPStreamableHttp(
                name="linear",
                transport="streamable-http",
                url="https://other.example.com/mcp",
            )
        ]
    )

    with pytest.raises(MCPServerAddError, match="name `linear` is already configured"):
        persist_oauth_mcp_server(
            config, url="https://mcp.example.com/mcp", name="linear"
        )


def test_add_oauth_mcp_server_persists_loadable_oauth_config() -> None:
    persist_oauth_mcp_server(
        build_test_vibe_config(),
        url="https://mcp.example.com/mcp",
        name="docs",
        scopes=["read", "write"],
    )

    server = VibeConfig.load().mcp_servers[0]

    assert isinstance(server, MCPStreamableHttp)
    assert server.name == "docs"
    assert isinstance(server.auth, MCPOAuth)
    assert server.auth.scopes == ["read", "write"]


def test_add_oauth_mcp_server_persists_http_transport() -> None:
    persist_oauth_mcp_server(
        build_test_vibe_config(),
        url="https://mcp.example.com/mcp",
        name="docs",
        transport="http",
    )

    server = VibeConfig.load().mcp_servers[0]

    assert isinstance(server, MCPHttp)
    assert server.transport == "http"
    assert isinstance(server.auth, MCPOAuth)


def test_parse_mcp_add_transport_rejects_unsupported_transport() -> None:
    with pytest.raises(MCPServerAddError, match="http, streamable-http"):
        parse_mcp_add_transport("sse")


def test_vibe_config_rejects_duplicate_mcp_server_names() -> None:
    with pytest.raises(ValueError, match="Duplicate MCP server name found: 'figma'"):
        VibeConfig.model_validate({
            "mcp_servers": [
                {
                    "name": "figma",
                    "transport": "streamable-http",
                    "url": "https://a.example.com/mcp",
                },
                {
                    "name": "figma",
                    "transport": "streamable-http",
                    "url": "https://b.example.com/mcp",
                },
            ]
        })
