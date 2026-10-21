from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
import tomllib
from typing import Never

import pytest
import pytest_asyncio

from vibe.app_server._mcp_auth import MCPAuthenticationService
from vibe.app_server._model import validate_wire
from vibe.app_server._runtime import HarnessProcess, create_harness_server
from vibe.app_server.client import AppServerClient
from vibe.app_server.mcp_catalog import MCPCatalogService
from vibe.app_server.protocol import (
    AppServerResponseError,
    ClientInfo,
    MCPAddParams,
    MCPAddResponse,
    ProtocolErrorCode,
)
from vibe.app_server.transport import memory_transport_pair
from vibe.core.config import MCPStaticAuth, MCPStdio, build_user_config_orchestrator


@pytest_asyncio.fixture
async def mcp_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AppServerClient]:
    async def build_session_context(*args: object, **kwargs: object) -> Never:
        raise AssertionError("MCP add must not create a session")

    async def login(*args: object, **kwargs: object) -> Never:
        raise AssertionError("MCP add must not start OAuth login")

    monkeypatch.setattr("vibe.app_server._mcp_auth.perform_oauth_login", login)
    client_transport, server_transport = memory_transport_pair()
    process = HarnessProcess()
    monkeypatch.setattr(process, "build_unified_session_context", build_session_context)
    process.mcp_catalog = MCPCatalogService(
        MCPAuthenticationService(),
        sessionless_catalog_factory=build_user_config_orchestrator,
    )
    server = await create_harness_server(
        server_transport, transport_kind="in_process", process=process
    )
    client = AppServerClient(client_transport, run_peer=server.serve)
    try:
        await client.initialize(ClientInfo(name="test", version="1"))
        await client.notify("initialized")
        yield client
    finally:
        await client.close()
        await process.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["!!!", "---", ""])
async def test_mcp_add_invalid_name_has_actionable_error(
    mcp_client: AppServerClient, name: str
) -> None:
    with pytest.raises(AppServerResponseError) as exc_info:
        await mcp_client.request(
            "mcp_catalog/add",
            {
                "url": "https://mcp.example.com/mcp",
                "name": name,
                "scopes": [],
                "transport": "streamable-http",
            },
        )

    assert exc_info.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert (
        exc_info.value.error.message
        == "MCP server name must contain letters or numbers."
    )


@pytest.mark.asyncio
async def test_canonical_mistral_static_add_persists_only_user_config(
    mcp_client: AppServerClient, config_dir: Path, tmp_working_directory: Path
) -> None:
    project_config = tmp_working_directory / ".vibe" / "config.toml"
    project_config.parent.mkdir()
    project_config.write_text('active_model = "project-model"\n', encoding="utf-8")
    original_project = project_config.read_bytes()
    params = {
        "name": "mistralai",
        "url": "https://api.mistral.ai/mcp",
        "transport": "streamable-http",
        "apiKeyEnv": "MISTRAL_API_KEY",
    }

    response = await mcp_client.request("mcp_catalog/add", params)

    assert response == {
        "name": "mistralai",
        "url": "https://api.mistral.ai/mcp",
        "created": True,
        "runtime": None,
    }
    config_path = config_dir / "config.toml"
    persisted = tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["mcp_servers"] == [
        {
            "name": "mistralai",
            "url": "https://api.mistral.ai/mcp",
            "transport": "streamable-http",
            "auth": {"type": "static", "api_key_env": "MISTRAL_API_KEY"},
        }
    ]
    original_user = config_path.read_bytes()
    assert await mcp_client.request("mcp_catalog/add", params) == {
        **response,
        "created": False,
    }
    assert config_path.read_bytes() == original_user
    assert project_config.read_bytes() == original_project


@pytest.mark.asyncio
async def test_stdio_add_persists_args_env_and_timeouts(
    mcp_client: AppServerClient, config_dir: Path
) -> None:
    params = {
        "name": "local",
        "transport": "stdio",
        "command": "uvx",
        "args": ["mcp-server-fetch", "--flag=value"],
        "env": {
            "DEBUG": "1",
            "TOKEN": "foo=bar",
            "EMPTY": "",
            "ProgramFiles(x86)": "some folder",
            "PADDED": "  value  ",
        },
        "startupTimeoutSec": 12.5,
        "toolTimeoutSec": 120,
    }
    response = await mcp_client.request("mcp_catalog/add", params)
    assert response == {"name": "local", "url": None, "created": True, "runtime": None}
    server = (await build_user_config_orchestrator()).config.mcp_servers[0]
    assert server == MCPStdio(
        name="local",
        transport="stdio",
        command="uvx",
        args=["mcp-server-fetch", "--flag=value"],
        env={
            "DEBUG": "1",
            "TOKEN": "foo=bar",
            "EMPTY": "",
            "ProgramFiles(x86)": "some folder",
            "PADDED": "  value  ",
        },
        startup_timeout_sec=12.5,
        tool_timeout_sec=120,
    )
    original = (config_dir / "config.toml").read_bytes()
    assert (await mcp_client.request("mcp_catalog/add", params))["created"] is False
    assert (config_dir / "config.toml").read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["http", "streamable-http"])
@pytest.mark.parametrize(
    "auth_options, expected_auth",
    [
        ({}, {"type": "oauth", "scopes": ["read"]}),
        (
            {"headers": {"X-Client": "vibe"}},
            {"type": "static", "headers": {"X-Client": "vibe"}},
        ),
        (
            {
                "apiKeyEnv": "SERVICE_TOKEN",
                "apiKeyHeader": "X-API-Key",
                "apiKeyFormat": "Token {token}",
                "headers": {"X-Client": "vibe"},
            },
            {
                "type": "static",
                "api_key_env": "SERVICE_TOKEN",
                "api_key_header": "X-API-Key",
                "api_key_format": "Token {token}",
                "headers": {"X-Client": "vibe"},
            },
        ),
    ],
)
async def test_remote_add_persists_auth_and_timeouts(
    mcp_client: AppServerClient,
    config_dir: Path,
    transport,
    auth_options,
    expected_auth,
) -> None:
    params = {
        "name": "example",
        "url": "https://EXAMPLE.test:443/mcp/",
        "transport": transport,
        "startupTimeoutSec": 15,
        "toolTimeoutSec": 90.5,
        "scopes": [] if auth_options else ["read"],
        **auth_options,
    }
    result = await mcp_client.request("mcp_catalog/add", params)
    assert result["url"] == "https://example.test/mcp/"
    persisted = tomllib.loads((config_dir / "config.toml").read_text(encoding="utf-8"))
    assert persisted["mcp_servers"][0] == {
        "name": "example",
        "url": "https://example.test/mcp/",
        "transport": transport,
        "auth": expected_auth,
        "startup_timeout_sec": 15.0,
        "tool_timeout_sec": 90.5,
    }
    assert (await mcp_client.request("mcp_catalog/add", params))["created"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["mcp_catalog/add", "mcp/add"])
async def test_legacy_oauth_add_preserves_wire_shape_and_url_idempotence(
    mcp_client: AppServerClient, config_dir: Path, method: str
) -> None:
    params = {
        "url": "http://mcp.example.test/mcp",
        "scopes": ["read"],
        "transport": "http",
        "allowInsecureHttp": True,
    }
    result = await mcp_client.request(method, params)
    assert result == {
        "name": "example",
        "url": params["url"],
        "created": True,
        "runtime": None,
    }
    persisted = (config_dir / "config.toml").read_bytes()
    assert (await mcp_client.request(method, {**params, "scopes": ["write"]}))[
        "created"
    ] is False
    assert (config_dir / "config.toml").read_bytes() == persisted
    config = tomllib.loads(persisted.decode())
    assert config["mcp_servers"][0]["auth"] == {"type": "oauth", "scopes": ["read"]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options, error",
    [
        ({"url": None}, "url"),
        ({"url": " "}, "URL"),
        ({"command": "uvx"}, "stdio"),
        ({"args": ["server"]}, "stdio"),
        ({"env": {"A": "1"}}, "stdio"),
        ({"apiKeyHeader": "X-API-Key"}, "apiKeyEnv"),
        ({"apiKeyFormat": "{token}"}, "apiKeyEnv"),
        ({"apiKeyEnv": ""}, "apiKeyEnv"),
        ({"apiKeyEnv": "BAD-NAME"}, "environment variable"),
        ({"apiKeyEnv": "TOKEN", "scopes": ["read"]}, "scopes"),
        ({"headers": {"X-Client": "vibe"}, "scopes": ["read"]}, "scopes"),
        ({"headers": {"X-Client": "one", "x-client": "two"}}, "Duplicate"),
        ({"headers": {"": "one"}}, "header"),
        ({"headers": {"Bad Name": "one"}}, "header"),
        ({"headers": {"authorization": "one"}, "apiKeyEnv": "TOKEN"}, "API key header"),
        (
            {
                "headers": {"x-api-key": "one"},
                "apiKeyEnv": "TOKEN",
                "apiKeyHeader": "X-API-Key",
            },
            "API key header",
        ),
        ({"apiKeyEnv": "TOKEN", "apiKeyFormat": "{{token}}"}, "placeholder"),
        ({"startupTimeoutSec": 0}, "greater than 0"),
        ({"toolTimeoutSec": -1}, "greater than 0"),
        ({"apiKeyEnv": "TOKEN", "toolTimeoutSec": 0}, "greater than 0"),
        ({"url": "http://example.test/mcp", "apiKeyEnv": "TOKEN"}, "https"),
        (
            {
                "url": "https://user:password@example.test/mcp",
                "headers": {"X-Client": "vibe"},
            },
            "credentials",
        ),
        ({"url": "https://example.test/mcp#fragment"}, "fragment"),
        ({"name": "---"}, "letters or numbers"),
    ],
)
async def test_invalid_remote_add_never_writes(
    mcp_client: AppServerClient, config_dir: Path, options, error: str
) -> None:
    original = (config_dir / "config.toml").read_bytes()
    with pytest.raises(AppServerResponseError, match=error) as exc:
        await mcp_client.request(
            "mcp_catalog/add",
            {"name": "example", "url": "https://example.test/mcp", **options},
        )
    assert exc.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert (config_dir / "config.toml").read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options, error",
    [
        ({"name": None}, "name"),
        ({"command": None}, "command"),
        ({"command": " "}, "command"),
        ({"command": "'"}, "quotation"),
        ({"url": "https://example.test/mcp"}, "stdio"),
        ({"headers": {"X-Client": "vibe"}}, "stdio"),
        ({"apiKeyEnv": "TOKEN"}, "stdio"),
        ({"apiKeyHeader": "Authorization"}, "stdio"),
        ({"apiKeyFormat": "{token}"}, "stdio"),
        ({"scopes": ["read"]}, "stdio"),
        ({"allowInsecureHttp": True}, "stdio"),
        ({"env": {"": "one"}}, "environment variable"),
        ({"env": {"BAD\x00NAME": "one"}}, "environment variable"),
        ({"startupTimeoutSec": 0}, "greater than 0"),
        ({"toolTimeoutSec": -1}, "greater than 0"),
    ],
)
async def test_invalid_stdio_add_never_writes(
    mcp_client: AppServerClient, config_dir: Path, options, error: str
) -> None:
    original = (config_dir / "config.toml").read_bytes()
    with pytest.raises(AppServerResponseError, match=error) as exc:
        await mcp_client.request(
            "mcp_catalog/add",
            {"name": "local", "transport": "stdio", "command": "uvx", **options},
        )
    assert exc.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert (config_dir / "config.toml").read_bytes() == original


@pytest.mark.asyncio
async def test_static_add_keeps_plain_http_security_opt_in(
    mcp_client: AppServerClient,
) -> None:
    await mcp_client.request(
        "mcp_catalog/add",
        {
            "name": "local",
            "url": "http://example.test/mcp",
            "headers": {"X-Client": "vibe"},
            "allowInsecureHttp": True,
        },
    )
    server = (await build_user_config_orchestrator()).config.mcp_servers[0]
    assert not isinstance(server, MCPStdio)
    assert isinstance(server.auth, MCPStaticAuth)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {
            "name": "remote",
            "url": "https://example.test/mcp",
            "headers": {"X-Client": "original"},
        },
        {"name": "local", "transport": "stdio", "command": "uvx", "args": ["original"]},
    ],
)
async def test_conflicting_add_does_not_overwrite_configuration(
    mcp_client: AppServerClient, config_dir: Path, params
) -> None:
    await mcp_client.request("mcp_catalog/add", params)
    original = (config_dir / "config.toml").read_bytes()
    changed = (
        {"headers": {"X-Client": "changed"}}
        if "url" in params
        else {"args": ["changed"]}
    )
    with pytest.raises(AppServerResponseError) as exc:
        await mcp_client.request("mcp_catalog/add", {**params, **changed})
    assert exc.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert (config_dir / "config.toml").read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "options",
    [
        {"name": "other"},
        {"url": "https://other.test/mcp"},
        {"headers": {}, "apiKeyEnv": None},
        {"toolTimeoutSec": 20},
    ],
)
async def test_remote_name_url_auth_and_timeout_conflicts_do_not_write(
    mcp_client: AppServerClient, config_dir: Path, options
) -> None:
    params = {
        "name": "example",
        "url": "https://example.test/mcp",
        "apiKeyEnv": "SERVICE_TOKEN",
    }
    await mcp_client.request("mcp_catalog/add", params)
    original = (config_dir / "config.toml").read_bytes()
    with pytest.raises(AppServerResponseError) as exc:
        await mcp_client.request("mcp_catalog/add", {**params, **options})
    assert exc.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert (config_dir / "config.toml").read_bytes() == original


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {
            "url": "https://example.test/mcp",
            "headers": {"X-Key": "secret-marker"},
            "startupTimeoutSec": 0,
        },
        {
            "url": "https://example.test/mcp",
            "headers": {"X-Key": "secret-marker", "x-key": "other"},
        },
        {"url": "https://example.test/mcp", "headers": ["secret-marker"]},
        {"url": "https://example.test/mcp", "headers": [{"token": "secret-marker"}]},
        {
            "url": "https://example.test/mcp",
            "headers": {"X-Key": "secret-marker"},
            "apiKeyEnv": "TOKEN",
            "apiKeyFormat": "{invalid}",
        },
        {
            "transport": "stdio",
            "command": "uvx",
            "env": {"TOKEN": "secret-marker"},
            "toolTimeoutSec": 0,
        },
        {"transport": "stdio", "command": "uvx", "env": ["secret-marker"]},
    ],
)
async def test_invalid_add_redacts_secret_values_in_response_and_logs(
    mcp_client: AppServerClient,
    config_dir: Path,
    caplog: pytest.LogCaptureFixture,
    params,
) -> None:
    original = (config_dir / "config.toml").read_bytes()
    with pytest.raises(AppServerResponseError) as exc:
        await mcp_client.request("mcp_catalog/add", {"name": "example", **params})
    assert exc.value.error.code is ProtocolErrorCode.INVALID_PARAMS
    assert "secret-marker" not in exc.value.error.model_dump_json()
    assert "secret-marker" not in caplog.text
    assert (config_dir / "config.toml").read_bytes() == original


def test_mcp_add_schema_keeps_new_fields_optional() -> None:
    schema = MCPAddParams.model_json_schema(by_alias=True)
    assert schema.get("required", []) == []
    assert set(schema["properties"]) == {
        "sessionId",
        "url",
        "name",
        "scopes",
        "transport",
        "allowInsecureHttp",
        "command",
        "args",
        "env",
        "headers",
        "apiKeyEnv",
        "apiKeyHeader",
        "apiKeyFormat",
        "startupTimeoutSec",
        "toolTimeoutSec",
    }
    defaults = MCPAddParams(url="https://example.test/mcp").model_dump(by_alias=True)
    assert defaults["command"] is None
    assert defaults["args"] == []
    assert defaults["env"] == defaults["headers"] == {}
    assert (
        defaults["apiKeyEnv"]
        is defaults["apiKeyHeader"]
        is defaults["apiKeyFormat"]
        is None
    )
    assert defaults["startupTimeoutSec"] is defaults["toolTimeoutSec"] is None


def test_mcp_add_wire_fields_are_additive_and_camel_case() -> None:
    old = validate_wire(MCPAddParams, {"url": "https://example.test/mcp"})
    assert old.transport == "streamable-http"
    assert old.scopes == []
    new = validate_wire(
        MCPAddParams,
        {
            "name": "local",
            "transport": "stdio",
            "command": "uvx",
            "args": ["server"],
            "env": {"A": "1"},
            "startupTimeoutSec": 10,
            "toolTimeoutSec": 20,
        },
    )
    assert new.url is None
    assert new.startup_timeout_sec == 10
    assert new.tool_timeout_sec == 20
    assert MCPAddResponse.model_validate({"name": "local", "created": True}).url is None


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [{"transport": "http"}, {"scopes": ["write"]}])
async def test_named_oauth_readd_returns_existing_server(
    mcp_client: AppServerClient, config_dir: Path, changed
) -> None:
    params = {
        "name": "docs",
        "url": "https://example.test/mcp",
        "transport": "streamable-http",
        "scopes": ["read"],
    }
    await mcp_client.request("mcp_catalog/add", params)
    original = (config_dir / "config.toml").read_bytes()

    response = await mcp_client.request("mcp_catalog/add", {**params, **changed})

    assert response["name"] == "docs"
    assert response["created"] is False
    assert (config_dir / "config.toml").read_bytes() == original


@pytest.mark.asyncio
async def test_unnamed_unauthenticated_add_accepts_timeouts_and_readds_by_url(
    mcp_client: AppServerClient, config_dir: Path
) -> None:
    params = {"url": "https://mcp.example.test/mcp", "startupTimeoutSec": 15}

    response = await mcp_client.request("mcp_catalog/add", params)

    assert response["name"] == "example"
    assert response["created"] is True
    persisted = (config_dir / "config.toml").read_bytes()
    server = tomllib.loads(persisted.decode())["mcp_servers"][0]
    assert server["auth"] == {"type": "static"}
    assert server["startup_timeout_sec"] == 15.0
    readd = await mcp_client.request(
        "mcp_catalog/add", {**params, "name": "example", "startupTimeoutSec": 25}
    )
    assert readd["created"] is False
    assert (config_dir / "config.toml").read_bytes() == persisted


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params",
    [
        {"name": "docs", "url": "https://example.test/mcp", "scopes": ["read"]},
        {"name": "docs", "url": "https://example.test/mcp", "apiKeyEnv": "TOKEN"},
        {"name": "docs", "transport": "stdio", "command": "uvx"},
    ],
)
async def test_readd_ignores_user_managed_server_state(
    mcp_client: AppServerClient, config_dir: Path, params
) -> None:
    await mcp_client.request("mcp_catalog/add", params)
    orchestrator = await build_user_config_orchestrator()
    await orchestrator.set_field(
        "/mcp_servers/0/disabled_tools", ["search"], reason="disable tool"
    )
    original = (config_dir / "config.toml").read_bytes()

    response = await mcp_client.request("mcp_catalog/add", params)

    assert response["created"] is False
    assert (config_dir / "config.toml").read_bytes() == original
