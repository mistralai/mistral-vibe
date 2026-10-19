from __future__ import annotations

from pathlib import Path
import tomllib
from unittest.mock import AsyncMock

import keyring
import keyring.backends.fail
import pytest

from vibe.app_server.mcp_catalog import create_sessionless_mcp_catalog
from vibe.core.auth import MCPOAuthError
from vibe.core.config import MCPOAuth, MCPStreamableHttp
from vibe.core.config.default_orchestrator import build_user_config_orchestrator
from vibe.core.config.mcp_servers import MCPServerRemoveError, persist_remote_mcp_server


def _oauth_server() -> MCPStreamableHttp:
    return MCPStreamableHttp(
        name="linear",
        transport="streamable-http",
        url="https://mcp.linear.app/mcp",
        auth=MCPOAuth(type="oauth", scopes=[]),
    )


def _persisted_servers(config_dir: Path) -> list[dict[str, object]]:
    with (config_dir / "config.toml").open("rb") as file:
        return tomllib.load(file).get("mcp_servers", [])


@pytest.mark.asyncio
async def test_add_keeps_config_when_login_fails(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    catalog = create_sessionless_mcp_catalog(build_user_config_orchestrator)
    login_error = MCPOAuthError("login failed")
    monkeypatch.setattr(
        "vibe.app_server._mcp_auth.perform_oauth_login",
        AsyncMock(side_effect=login_error),
    )

    with pytest.raises(MCPOAuthError) as exc_info:
        await catalog.add_server(_oauth_server(), login=True, on_oauth_url=AsyncMock())

    assert exc_info.value is login_error
    assert _persisted_servers(config_dir)[0]["name"] == "linear"


@pytest.mark.asyncio
async def test_remove_keeps_config_when_credential_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    # Credentials are deleted before the config entry, so a cleanup failure leaves
    # the config untouched (no removal, no restore).
    catalog = create_sessionless_mcp_catalog(build_user_config_orchestrator)
    await persist_remote_mcp_server(
        await build_user_config_orchestrator(), _oauth_server()
    )
    monkeypatch.setattr(
        "vibe.app_server._mcp_auth.delete_oauth_credentials",
        AsyncMock(side_effect=MCPOAuthError("keyring unavailable")),
    )

    with pytest.raises(MCPServerRemoveError, match="keyring unavailable"):
        await catalog.remove_server("linear")

    assert _persisted_servers(config_dir)[0]["name"] == "linear"


@pytest.mark.asyncio
async def test_remove_succeeds_without_keyring_backend(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    # On a headless system (CI, containers) there is no keyring backend, which is
    # exactly where `vibe mcp add ... --no-login` is the intended flow. Removing
    # such a server must succeed rather than fail with an unusable-keyring error.
    catalog = create_sessionless_mcp_catalog(build_user_config_orchestrator)
    await persist_remote_mcp_server(
        await build_user_config_orchestrator(), _oauth_server()
    )
    monkeypatch.setattr(keyring, "get_keyring", keyring.backends.fail.Keyring)

    result = await catalog.remove_server("linear")

    assert result.removed
    assert _persisted_servers(config_dir) == []
