from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError
import pytest

from vibe.core.config import MCPHttp, MCPOAuth, MCPStaticAuth, MCPStreamableHttp
from vibe.core.tools.mcp import AuthStatus
from vibe.core.tools.mcp.registry import MCPRegistry

HTTP_TRANSPORTS = [
    pytest.param(MCPHttp, "http", id="http"),
    pytest.param(MCPStreamableHttp, "streamable-http", id="streamable-http"),
]


@pytest.mark.parametrize(("cls", "transport"), HTTP_TRANSPORTS)
def test_legacy_top_level_keys_promote_to_static_auth(
    cls: type[MCPHttp | MCPStreamableHttp],
    transport: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "secret-token")

    srv = cls.model_validate({
        "name": "remote",
        "transport": transport,
        "url": "https://mcp.example.com",
        "api_key_env": "MCP_TOKEN",
    })

    assert isinstance(srv.auth, MCPStaticAuth)
    assert srv.auth.api_key_env == "MCP_TOKEN"
    assert srv.http_headers() == {"Authorization": "Bearer secret-token"}


@pytest.mark.parametrize(("cls", "transport"), HTTP_TRANSPORTS)
def test_legacy_custom_header_and_format(
    cls: type[MCPHttp | MCPStreamableHttp],
    transport: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "k")

    srv = cls.model_validate({
        "name": "remote",
        "transport": transport,
        "url": "https://mcp.example.com",
        "api_key_env": "MCP_TOKEN",
        "api_key_header": "X-API-Key",
        "api_key_format": "{token}",
    })

    assert srv.http_headers() == {"X-API-Key": "k"}


def test_api_key_format_accepts_format_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_TOKEN", "k")
    auth = MCPStaticAuth.model_validate({
        "api_key_env": "MCP_TOKEN",
        "api_key_header": "X-API-Key",
        "api_key_format": "{token:>3}",
    })

    assert auth.http_headers() == {"X-API-Key": "  k"}


@pytest.mark.parametrize(("cls", "transport"), HTTP_TRANSPORTS)
def test_legacy_explicit_api_key_header_takes_precedence(
    cls: type[MCPHttp | MCPStreamableHttp],
    transport: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "environment-token")

    srv = cls.model_validate({
        "name": "remote",
        "transport": transport,
        "url": "https://mcp.example.com",
        "headers": {"authorization": "Bearer fixed-token"},
        "api_key_env": "MCP_TOKEN",
        "api_key_header": "Authorization",
    })

    assert srv.http_headers() == {"authorization": "Bearer fixed-token"}


def test_explicit_static_auth_header_takes_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MCP_TOKEN", "environment-token")
    auth = MCPStaticAuth.model_validate({
        "headers": {"Authorization": "Bearer fixed-token"},
        "api_key_env": "MCP_TOKEN",
    })

    assert auth.http_headers() == {"Authorization": "Bearer fixed-token"}


@pytest.mark.parametrize(("cls", "transport"), HTTP_TRANSPORTS)
def test_explicit_static_auth_round_trips(
    cls: type[MCPHttp | MCPStreamableHttp], transport: str
) -> None:
    srv = cls.model_validate({
        "name": "remote",
        "transport": transport,
        "url": "https://mcp.example.com",
        "auth": {"type": "static", "api_key_env": "X", "api_key_header": "X-API-Key"},
    })

    dumped = srv.model_dump()
    rebuilt = cls.model_validate(dumped)

    assert isinstance(rebuilt.auth, MCPStaticAuth)
    assert rebuilt.auth.api_key_env == "X"
    assert rebuilt.auth.api_key_header == "X-API-Key"


@pytest.mark.parametrize(("cls", "transport"), HTTP_TRANSPORTS)
def test_oauth_auth_parses(
    cls: type[MCPHttp | MCPStreamableHttp], transport: str
) -> None:
    srv = cls.model_validate({
        "name": "linear",
        "transport": transport,
        "url": "https://mcp.linear.app/mcp",
        "auth": {"type": "oauth", "scopes": ["read", "write"]},
    })

    assert isinstance(srv.auth, MCPOAuth)
    assert srv.auth.scopes == ["read", "write"]
    assert srv.auth.redirect_port == 47823
    assert srv.http_headers() == {}


@pytest.mark.parametrize(("cls", "transport"), HTTP_TRANSPORTS)
def test_mixing_legacy_keys_with_auth_block_is_rejected(
    cls: type[MCPHttp | MCPStreamableHttp], transport: str
) -> None:
    with pytest.raises(ValidationError, match="cannot mix top-level"):
        cls.model_validate({
            "name": "remote",
            "transport": transport,
            "url": "https://mcp.example.com",
            "api_key_env": "LEGACY",
            "auth": {"type": "static", "api_key_env": "NEW"},
        })


@pytest.mark.parametrize(("cls", "transport"), HTTP_TRANSPORTS)
def test_default_auth_is_static(
    cls: type[MCPHttp | MCPStreamableHttp], transport: str
) -> None:
    srv = cls.model_validate({
        "name": "remote",
        "transport": transport,
        "url": "https://mcp.example.com",
    })

    assert isinstance(srv.auth, MCPStaticAuth)
    assert srv.http_headers() == {}


def test_oauth_client_id_and_metadata_url_mutually_exclusive() -> None:
    with pytest.raises(ValidationError, match="mutually exclusive"):
        MCPOAuth.model_validate({
            "type": "oauth",
            "scopes": ["read"],
            "client_id": "abc",
            "client_metadata_url": "https://example.com/cm.json",
        })


def test_oauth_client_metadata_url_must_be_http_url() -> None:
    with pytest.raises(ValidationError):
        MCPOAuth.model_validate({
            "type": "oauth",
            "scopes": ["read"],
            "client_metadata_url": "not-a-url",
        })


def test_oauth_client_id_rejects_empty_string() -> None:
    with pytest.raises(ValidationError):
        MCPOAuth.model_validate({"type": "oauth", "scopes": ["read"], "client_id": ""})


@pytest.mark.parametrize("port", [80, 1023, 0, 65536, 70000])
def test_oauth_redirect_port_out_of_range(port: int) -> None:
    with pytest.raises(ValidationError):
        MCPOAuth.model_validate({
            "type": "oauth",
            "scopes": ["read"],
            "redirect_port": port,
        })


def test_oauth_redirect_port_inside_range() -> None:
    auth = MCPOAuth.model_validate({
        "type": "oauth",
        "scopes": ["read"],
        "redirect_port": 1024,
    })
    assert auth.redirect_port == 1024


def test_static_auth_forbids_extra_keys() -> None:
    with pytest.raises(ValidationError):
        MCPStaticAuth.model_validate({"type": "static", "headerz": {}})


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"api_key_env": "NOT VALID"}, "valid environment variable name"),
        (
            {"api_key_env": "TOKEN", "api_key_header": "Bad Header"},
            "valid HTTP header name",
        ),
        (
            {"api_key_env": "TOKEN", "api_key_format": "Bearer token"},
            "must contain the `{token}` placeholder",
        ),
        (
            {"api_key_env": "TOKEN", "api_key_format": "Bearer {token} {other}"},
            "may only reference the token placeholder",
        ),
        (
            {"api_key_env": "TOKEN", "api_key_format": "Bearer {{token}}"},
            "must contain the `{token}` placeholder",
        ),
        ({"headers": {"X-Tenant": "a", "x-tenant": "b"}}, "Duplicate HTTP header"),
    ],
)
def test_static_auth_rejects_invalid_configuration(
    values: dict[str, object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        MCPStaticAuth.model_validate(values)


def test_api_key_header_without_env_loads_and_warns(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        auth = MCPStaticAuth.model_validate({
            "api_key_header": "X-API-Key",
            "api_key_format": "{token}",
        })

    assert auth.api_key_header == "X-API-Key"
    assert auth.http_headers() == {}
    assert any("api_key_env" in record.message for record in caplog.records)


def test_default_static_auth_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        MCPStaticAuth.model_validate({})

    assert caplog.records == []


def test_oauth_forbids_extra_keys() -> None:
    with pytest.raises(ValidationError):
        MCPOAuth.model_validate({"type": "oauth", "scopes": ["read"], "scope": "x"})


def test_oauth_scopes_required() -> None:
    with pytest.raises(ValidationError):
        MCPOAuth.model_validate({"type": "oauth"})


def test_oauth_scopes_empty_list_allowed() -> None:
    auth = MCPOAuth.model_validate({"type": "oauth", "scopes": []})
    assert auth.scopes == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("cls", "transport"), HTTP_TRANSPORTS)
async def test_registry_marks_oauth_servers_without_tokens_as_needing_auth(
    cls: type[MCPHttp | MCPStreamableHttp], transport: str
) -> None:
    srv = cls.model_validate({
        "name": "linear",
        "transport": transport,
        "url": "https://mcp.linear.app/mcp",
        "auth": {"type": "oauth", "scopes": ["read"]},
    })
    registry = MCPRegistry()
    storage = MagicMock()
    storage.get_tokens = AsyncMock(return_value=None)
    storage.delete_tokens = AsyncMock()
    storage.delete_client_info = AsyncMock()

    with (
        patch("vibe.core.tools.mcp.registry.KeyringTokenStorage", return_value=storage),
        patch(
            "vibe.core.tools.mcp.registry.Fingerprint.load",
            new=AsyncMock(return_value=None),
        ),
        patch("vibe.core.tools.mcp.registry.Fingerprint.delete", new=AsyncMock()),
    ):
        first = await registry.get_tools_async([srv])
        second = await registry.get_tools_async([srv])

    assert first == {}
    assert second == {}
    assert registry.needs_auth == {"linear"}
    assert registry.status()["linear"] == AuthStatus.NEEDS_AUTH
