from __future__ import annotations

from pathlib import Path
import tomllib
from unittest.mock import AsyncMock

import pytest
import tomli_w

from tests.conftest import ConfigBuilder, OrchestratorLoader
from vibe.core.config import (
    MCPHttp,
    MCPOAuth,
    MCPStaticAuth,
    MCPStdio,
    MCPStreamableHttp,
    VibeConfigSchema,
)
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.layers.overrides import OverridesLayer
from vibe.core.config.layers.user import UserConfigLayer
from vibe.core.config.mcp_servers import (
    MCPServerAddError,
    persist_oauth_mcp_server,
    persist_remote_mcp_server,
    persist_stdio_mcp_server,
    remove_mcp_server,
)
from vibe.core.config.orchestrator import ConfigOrchestrator
from vibe.core.config.types import ConcurrencyConflictError


def _persisted_servers(config_dir: Path) -> list[dict]:
    with (config_dir / "config.toml").open("rb") as file:
        return tomllib.load(file).get("mcp_servers", [])


@pytest.mark.asyncio
async def test_persistence_preserves_concurrency_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    orchestrator = load_orchestrator(build_config())
    conflict = ConcurrencyConflictError("expected", "actual")
    monkeypatch.setattr(orchestrator, "set_field", AsyncMock(return_value=[conflict]))

    with pytest.raises(ConcurrencyConflictError) as exc_info:
        await persist_oauth_mcp_server(orchestrator, url="https://mcp.example.com/mcp")

    assert exc_info.value is conflict


@pytest.mark.parametrize(
    ("url", "expected_url"),
    [
        ("https://mcp.example.com/mcp", "https://mcp.example.com/mcp"),
        ("http://localhost:8000/mcp", "http://localhost:8000/mcp"),
        ("http://127.0.0.1:8000/mcp", "http://127.0.0.1:8000/mcp"),
        ("https://example.com:443/mcp?tenant=a", "https://example.com/mcp?tenant=a"),
    ],
)
@pytest.mark.asyncio
async def test_add_oauth_mcp_server_accepts_supported_urls(
    url: str,
    expected_url: str,
    config_dir: Path,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    result = await persist_oauth_mcp_server(load_orchestrator(build_config()), url=url)

    assert result.created is True
    assert result.server.url == expected_url
    assert _persisted_servers(config_dir)[0]["url"] == expected_url


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("mcp.example.com/mcp", "include a scheme"),
        ("ftp://mcp.example.com/mcp", "must use https"),
        ("https:///mcp", "include a host"),
        ("http://mcp.example.com/mcp", "unless it points to localhost"),
        ("https://mcp.example.com/mcp#section", "must not include a fragment"),
        ("https://mcp.example.com:abc/mcp", r"valid HTTP\(S\) URL"),
        ("https://mcp.example.com:99999/mcp", r"valid HTTP\(S\) URL"),
        ("https://user:secret@mcp.example.com/mcp", "must not include credentials"),
    ],
)
@pytest.mark.asyncio
async def test_add_oauth_mcp_server_rejects_invalid_urls(
    url: str,
    message: str,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    with pytest.raises(MCPServerAddError, match=message):
        await persist_oauth_mcp_server(load_orchestrator(build_config()), url=url)


@pytest.mark.parametrize(
    ("url", "expected_name"),
    [
        ("https://mcp.linear.app/mcp", "linear"),
        ("https://example.com/mcp", "example"),
        ("https://mcp.example.com/notion/mcp", "example"),
        ("https://mcp.localhost/notion/mcp", "notion"),
    ],
)
@pytest.mark.asyncio
async def test_add_oauth_mcp_server_generates_alias(
    url: str,
    expected_name: str,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    result = await persist_oauth_mcp_server(load_orchestrator(build_config()), url=url)

    assert result.server.name == expected_name


@pytest.mark.asyncio
async def test_add_oauth_mcp_server_suffixes_generated_alias_collision(
    config_dir: Path,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    orchestrator = load_orchestrator(
        build_config(
            mcp_servers=[
                MCPStreamableHttp(
                    name="linear",
                    transport="streamable-http",
                    url="https://other.example.com/mcp",
                )
            ]
        )
    )

    result = await persist_oauth_mcp_server(
        orchestrator, url="https://mcp.linear.app/mcp"
    )

    assert result.server.name == "linear_2"
    assert _persisted_servers(config_dir)[-1]["name"] == "linear_2"


@pytest.mark.asyncio
async def test_add_oauth_mcp_server_is_idempotent_for_existing_url(
    config_dir: Path,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    first = await persist_oauth_mcp_server(
        load_orchestrator(build_config()), url="https://mcp.linear.app/mcp"
    )
    second = await persist_oauth_mcp_server(
        await build_default_orchestrator(), url="https://mcp.linear.app:443/mcp/"
    )

    assert first.created is True
    assert second.created is False
    assert second.server.name == "linear"
    assert len(_persisted_servers(config_dir)) == 1


@pytest.mark.asyncio
async def test_add_oauth_mcp_server_rejects_active_static_url_match(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    orchestrator = load_orchestrator(
        build_config(
            mcp_servers=[
                MCPStreamableHttp(
                    name="docs",
                    transport="streamable-http",
                    url="https://mcp.example.com/mcp",
                )
            ]
        )
    )

    with pytest.raises(MCPServerAddError, match="`docs` with static auth"):
        await persist_oauth_mcp_server(orchestrator, url="https://mcp.example.com/mcp")


@pytest.mark.asyncio
async def test_add_oauth_mcp_server_rejects_persisted_static_url_match(
    config_dir: Path,
) -> None:
    config_file = config_dir / "config.toml"
    with config_file.open("rb") as f:
        data = tomllib.load(f)
    data["mcp_servers"] = [
        {
            "name": "docs",
            "transport": "streamable-http",
            "url": "https://mcp.example.com/mcp",
        }
    ]
    with config_file.open("wb") as f:
        tomli_w.dump(data, f)

    with pytest.raises(MCPServerAddError, match="`docs` with static auth"):
        await persist_oauth_mcp_server(
            await build_default_orchestrator(), url="https://mcp.example.com/mcp"
        )


@pytest.mark.asyncio
async def test_add_oauth_mcp_server_rejects_existing_url_with_different_name(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    await persist_oauth_mcp_server(
        load_orchestrator(build_config()), url="https://mcp.linear.app/mcp"
    )

    with pytest.raises(MCPServerAddError, match="already configured as `linear`"):
        await persist_oauth_mcp_server(
            await build_default_orchestrator(),
            url="https://mcp.linear.app/mcp",
            name="other",
        )


@pytest.mark.asyncio
async def test_add_oauth_mcp_server_rejects_explicit_alias_collision(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    orchestrator = load_orchestrator(
        build_config(
            mcp_servers=[
                MCPStreamableHttp(
                    name="linear",
                    transport="streamable-http",
                    url="https://other.example.com/mcp",
                )
            ]
        )
    )

    with pytest.raises(MCPServerAddError, match="name `linear` is already configured"):
        await persist_oauth_mcp_server(
            orchestrator, url="https://mcp.example.com/mcp", name="linear"
        )


@pytest.mark.asyncio
async def test_add_oauth_mcp_server_persists_loadable_oauth_config(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    await persist_oauth_mcp_server(
        load_orchestrator(build_config()),
        url="https://mcp.example.com/mcp",
        name="docs",
        scopes=["read", "write"],
    )

    server = (await build_default_orchestrator()).config.mcp_servers[0]

    assert isinstance(server, MCPStreamableHttp)
    assert server.name == "docs"
    assert isinstance(server.auth, MCPOAuth)
    assert server.auth.scopes == ["read", "write"]


@pytest.mark.asyncio
async def test_add_oauth_mcp_server_persists_http_transport(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    await persist_oauth_mcp_server(
        load_orchestrator(build_config()),
        url="https://mcp.example.com/mcp",
        name="docs",
        transport="http",
    )

    server = (await build_default_orchestrator()).config.mcp_servers[0]

    assert isinstance(server, MCPHttp)
    assert server.transport == "http"
    assert isinstance(server.auth, MCPOAuth)


@pytest.mark.asyncio
async def test_persist_remote_mcp_server_writes_only_persistence_layer(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    with config_path.open("rb") as file:
        user_config = tomllib.load(file)
    user_config["mcp_servers"] = [
        {
            "name": "user-server",
            "transport": "streamable-http",
            "url": "https://user.example/mcp",
        }
    ]
    with config_path.open("wb") as file:
        tomli_w.dump(user_config, file)

    user_layer = UserConfigLayer(path=config_path)
    override_layer = OverridesLayer(
        data={
            "mcp_servers": [
                {
                    "name": "runtime-server",
                    "transport": "streamable-http",
                    "url": "https://runtime.example/mcp",
                }
            ]
        }
    )
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[user_layer, override_layer],
        default_layer_resolver=lambda: user_layer,
    )
    server = MCPStreamableHttp(
        name="added-server",
        transport="streamable-http",
        url="https://added.example/mcp",
        auth=MCPStaticAuth(),
    )

    await persist_remote_mcp_server(orchestrator, server)

    assert [server["name"] for server in _persisted_servers(config_dir)] == [
        "user-server",
        "added-server",
    ]
    assert {server.name for server in orchestrator.config.mcp_servers} == {
        "user-server",
        "runtime-server",
        "added-server",
    }


@pytest.mark.asyncio
async def test_persist_remote_mcp_server_validates_persistence_not_merged_config(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    user_layer = UserConfigLayer(path=config_path)
    override_layer = OverridesLayer(data={"theme": "dracula"})
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[user_layer, override_layer],
        default_layer_resolver=lambda: user_layer,
    )
    override_layer._data["mcp_servers"] = [
        {
            "name": "docs",
            "transport": "streamable-http",
            "url": "https://runtime.example/mcp",
        }
    ]
    server = MCPStreamableHttp(
        name="docs", transport="streamable-http", url="https://user.example/mcp"
    )

    result = await persist_remote_mcp_server(orchestrator, server)

    assert result.created is True
    assert _persisted_servers(config_dir)[0]["url"] == "https://user.example/mcp"
    effective_server = orchestrator.config.mcp_servers[0]
    assert isinstance(effective_server, MCPStreamableHttp)
    assert effective_server.url == "https://runtime.example/mcp"


@pytest.mark.parametrize(
    "legacy_url",
    [
        "http://192.168.1.10:8000/mcp",
        "https://user:secret@legacy.example.com/mcp",
        "https://legacy.example.com/mcp#section",
    ],
)
@pytest.mark.asyncio
async def test_persist_remote_mcp_server_ignores_legacy_url_policy_violations(
    legacy_url: str,
    build_config: ConfigBuilder,
    load_orchestrator: OrchestratorLoader[VibeConfigSchema],
) -> None:
    orchestrator = load_orchestrator(
        build_config(
            mcp_servers=[
                MCPStreamableHttp(
                    name="legacy", transport="streamable-http", url=legacy_url
                )
            ]
        )
    )
    server = MCPStreamableHttp(
        name="added", transport="streamable-http", url="https://added.example.com/mcp"
    )

    result = await persist_remote_mcp_server(orchestrator, server)

    assert result.created is True
    assert result.server == server


@pytest.mark.asyncio
async def test_remove_mcp_server_only_removes_persistence_layer_entry(
    config_dir: Path,
) -> None:
    config_path = config_dir / "config.toml"
    with config_path.open("rb") as file:
        user_config = tomllib.load(file)
    user_config["mcp_servers"] = [
        {
            "name": "shared",
            "transport": "streamable-http",
            "url": "https://user.example/mcp",
        }
    ]
    with config_path.open("wb") as file:
        tomli_w.dump(user_config, file)

    user_layer = UserConfigLayer(path=config_path)
    override_layer = OverridesLayer(
        data={
            "mcp_servers": [
                {
                    "name": "shared",
                    "transport": "streamable-http",
                    "url": "https://runtime.example/mcp",
                }
            ]
        }
    )
    orchestrator = await ConfigOrchestrator.create(
        schema=VibeConfigSchema,
        layers=[user_layer, override_layer],
        default_layer_resolver=lambda: user_layer,
    )

    result = await remove_mcp_server(orchestrator, "shared")

    assert result.removed is True
    assert isinstance(result.server, MCPStreamableHttp)
    assert result.server.url == "https://user.example/mcp"
    assert _persisted_servers(config_dir) == []
    effective_server = orchestrator.config.mcp_servers[0]
    assert isinstance(effective_server, MCPStreamableHttp)
    assert effective_server.url == "https://runtime.example/mcp"


@pytest.mark.asyncio
async def test_persist_stdio_mcp_server_is_idempotent_for_identical_server(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    orchestrator = load_orchestrator(build_config())
    server = MCPStdio(
        name="local", transport="stdio", command="python", args=["-m", "server"]
    )

    first = await persist_stdio_mcp_server(orchestrator, server)
    second = await persist_stdio_mcp_server(orchestrator, server)

    assert first.created is True
    assert second.created is False
    stored = orchestrator.config.mcp_servers
    assert len(stored) == 1
    assert stored[0].name == "local"


@pytest.mark.asyncio
async def test_persist_stdio_mcp_server_rejects_name_collision_with_different_options(
    build_config: ConfigBuilder, load_orchestrator: OrchestratorLoader[VibeConfigSchema]
) -> None:
    orchestrator = load_orchestrator(build_config())
    await persist_stdio_mcp_server(
        orchestrator,
        MCPStdio(name="local", transport="stdio", command="python", args=["a"]),
    )

    with pytest.raises(MCPServerAddError, match="already configured"):
        await persist_stdio_mcp_server(
            orchestrator,
            MCPStdio(name="local", transport="stdio", command="python", args=["b"]),
        )


def test_parse_mcp_add_transport_rejects_unsupported_transport() -> None:
    from vibe.utils.mcp import parse_mcp_add_transport

    with pytest.raises(ValueError, match="http, streamable-http"):
        parse_mcp_add_transport("sse")


def test_vibe_config_rejects_duplicate_mcp_server_names() -> None:
    with pytest.raises(ValueError, match="Duplicate name 'figma'"):
        VibeConfigSchema.model_validate({
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
