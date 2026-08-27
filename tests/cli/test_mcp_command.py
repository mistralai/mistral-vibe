from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tomllib
from unittest.mock import AsyncMock

import pytest
import tomli_w

from vibe.app_server import _mcp_auth, mcp_catalog
from vibe.cli import entrypoint, mcp_command as mcp_cli
from vibe.core.auth import MCPOAuthError
from vibe.core.config import MCPOAuth, MCPStaticAuth, MCPStdio, MCPStreamableHttp
from vibe.core.config.default_orchestrator import build_default_orchestrator
from vibe.core.config.types import ConcurrencyConflictError
from vibe.core.trusted_folders import trusted_folders_manager


def _run_mcp(*args: str) -> None:
    mcp_cli.run_mcp_cli(list(args))


def _persisted_servers(config_dir: Path) -> list[dict[str, object]]:
    with (config_dir / "config.toml").open("rb") as file:
        return tomllib.load(file).get("mcp_servers", [])


def _write_persisted_servers(
    config_dir: Path, servers: list[dict[str, object]]
) -> None:
    config_path = config_dir / "config.toml"
    with config_path.open("rb") as file:
        config = tomllib.load(file)
    config["mcp_servers"] = servers
    config_path.write_bytes(tomli_w.dumps(config).encode())


def test_mcp_add_persists_canonical_mistral_config(
    capsys: pytest.CaptureFixture[str], config_dir: Path
) -> None:
    _run_mcp(
        "add",
        "mistralai",
        "--url",
        "https://api.mistral.ai/mcp",
        "--transport",
        "streamable-http",
        "--api-key-env",
        "MISTRAL_API_KEY",
        "--api-key-header",
        "Authorization",
        "--api-key-format",
        "Bearer {token}",
    )

    assert capsys.readouterr().out == "Added MCP server `mistralai`.\n"
    # Default api_key_header/api_key_format are omitted; the model reapplies them.
    assert _persisted_servers(config_dir) == [
        {
            "name": "mistralai",
            "transport": "streamable-http",
            "url": "https://api.mistral.ai/mcp",
            "auth": {"type": "static", "api_key_env": "MISTRAL_API_KEY"},
        }
    ]


def test_mcp_add_round_trips_through_vibe_config(config_dir: Path) -> None:
    _run_mcp(
        "add",
        "mistralai",
        "--url",
        "https://api.mistral.ai/mcp",
        "--api-key-env",
        "MISTRAL_API_KEY",
    )

    server = asyncio.run(build_default_orchestrator()).config.mcp_servers[0]

    assert isinstance(server, MCPStreamableHttp)
    assert server.name == "mistralai"
    assert server.url == "https://api.mistral.ai/mcp"
    assert isinstance(server.auth, MCPStaticAuth)
    assert server.auth.api_key_env == "MISTRAL_API_KEY"
    assert server.auth.api_key_header == "Authorization"
    assert server.auth.api_key_format == "Bearer {token}"
    assert len(_persisted_servers(config_dir)) == 1


def test_mcp_add_stdio_persists_and_round_trips(
    capsys: pytest.CaptureFixture[str], config_dir: Path
) -> None:
    _run_mcp(
        "add",
        "files",
        "--transport",
        "stdio",
        "--command",
        "npx",
        "--arg=-y",
        "--arg",
        "@modelcontextprotocol/server-filesystem",
        "--env",
        "ROOT=/tmp",
    )

    assert capsys.readouterr().out == "Added MCP server `files`.\n"
    assert _persisted_servers(config_dir) == [
        {
            "name": "files",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem"],
            "env": {"ROOT": "/tmp"},
        }
    ]

    server = asyncio.run(build_default_orchestrator()).config.mcp_servers[0]
    assert isinstance(server, MCPStdio)
    assert server.argv() == ["npx", "-y", "@modelcontextprotocol/server-filesystem"]
    assert server.env == {"ROOT": "/tmp"}


def test_mcp_add_stdio_requires_command(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp("add", "files", "--transport", "stdio")

    assert exc_info.value.code == 2
    assert "--command is required for --transport stdio" in capsys.readouterr().err


def test_mcp_add_stdio_rejects_remote_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp(
            "add",
            "files",
            "--transport",
            "stdio",
            "--command",
            "npx",
            "--url",
            "https://mcp.example.com/mcp",
        )

    assert exc_info.value.code == 2
    assert "--url cannot be used with --transport stdio" in capsys.readouterr().err


def test_mcp_add_remote_rejects_stdio_options(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp(
            "add", "files", "--url", "https://mcp.example.com/mcp", "--command", "npx"
        )

    assert exc_info.value.code == 2
    assert (
        "--command can only be used with --transport stdio" in capsys.readouterr().err
    )


def test_mcp_add_remote_requires_url(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp("add", "docs")

    assert exc_info.value.code == 2
    assert "--url is required" in capsys.readouterr().err


def test_mcp_remove_deletes_stdio_server(
    capsys: pytest.CaptureFixture[str], config_dir: Path
) -> None:
    _run_mcp("add", "files", "--transport", "stdio", "--command", "npx")
    capsys.readouterr()

    _run_mcp("remove", "files")

    assert capsys.readouterr().out == "Removed MCP server `files`.\n"
    assert _persisted_servers(config_dir) == []


@pytest.mark.parametrize(
    "equivalent_url",
    [
        "https://api.mistral.ai/mcp",
        "https://api.mistral.ai/mcp/",
        "https://API.MISTRAL.AI:443/mcp",
    ],
)
def test_mcp_add_is_idempotent_for_equivalent_urls(
    equivalent_url: str, capsys: pytest.CaptureFixture[str], config_dir: Path
) -> None:
    args = (
        "add",
        "mistralai",
        "--url",
        "https://api.mistral.ai/mcp",
        "--api-key-env",
        "MISTRAL_API_KEY",
    )
    _run_mcp(*args)
    capsys.readouterr()

    _run_mcp(
        "add", "mistralai", "--url", equivalent_url, "--api-key-env", "MISTRAL_API_KEY"
    )

    assert capsys.readouterr().out == (
        "MCP server `mistralai` is already configured.\n"
    )
    assert len(_persisted_servers(config_dir)) == 1


def test_mcp_add_rejects_same_server_with_different_options(
    capsys: pytest.CaptureFixture[str], config_dir: Path
) -> None:
    _run_mcp(
        "add",
        "docs",
        "--url",
        "https://mcp.example.com/mcp",
        "--header",
        "X-Tenant=acme",
        "--startup-timeout-sec",
        "15",
    )
    capsys.readouterr()

    with pytest.raises(SystemExit) as exc_info:
        _run_mcp(
            "add",
            "docs",
            "--url",
            "https://mcp.example.com/mcp",
            "--header",
            "X-Tenant=acme",
        )

    assert exc_info.value.code == 2
    assert "already configured with different options" in capsys.readouterr().err
    assert _persisted_servers(config_dir)[0]["startup_timeout_sec"] == 15.0


def test_mcp_add_targets_user_config_when_project_config_exists(
    config_dir: Path, tmp_working_directory: Path
) -> None:
    project_config = tmp_working_directory / ".vibe" / "config.toml"
    project_config.parent.mkdir()
    project_config.write_bytes(tomli_w.dumps({"theme": "dracula"}).encode())
    trusted_folders_manager.add_trusted(project_config.parent)

    _run_mcp(
        "add",
        "mistralai",
        "--url",
        "https://api.mistral.ai/mcp",
        "--api-key-env",
        "MISTRAL_API_KEY",
    )

    assert len(_persisted_servers(config_dir)) == 1
    with project_config.open("rb") as file:
        assert tomllib.load(file) == {"theme": "dracula"}


def test_mcp_add_persists_oauth_without_login(config_dir: Path) -> None:
    _run_mcp("add", "linear", "--url", "https://mcp.linear.app/mcp", "--no-login")

    assert _persisted_servers(config_dir)[0]["auth"] == {"type": "oauth", "scopes": []}


def test_mcp_add_oauth_logs_in_and_opens_browser(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    config_dir: Path,
) -> None:
    """*Prepare*: A process-owned OAuth login emits one authorization URL.
    *Do*: Add an OAuth MCP source with immediate login.
    *Assert*: The CLI opens the URL and retains the canonical persisted source.
    """
    # Prepare
    opened_urls: list[str] = []

    async def fake_login(server, *, on_url) -> None:
        assert isinstance(server.auth, MCPOAuth)
        await on_url("https://auth.example/authorize")

    monkeypatch.setattr(_mcp_auth, "perform_oauth_login", fake_login)
    monkeypatch.setattr(
        mcp_cli.webbrowser, "open", lambda url: opened_urls.append(url) or True
    )

    # Do
    _run_mcp("add", "linear", "--url", "https://mcp.linear.app/mcp")

    # Assert
    output = capsys.readouterr().out
    assert "https://auth.example/authorize" in output
    assert output.startswith("Added MCP server `linear`.\n")
    assert output.endswith("OAuth login completed.\n")
    assert opened_urls == ["https://auth.example/authorize"]
    assert _persisted_servers(config_dir)[0]["auth"] == {"type": "oauth", "scopes": []}


def test_mcp_add_oauth_keeps_config_after_login_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    config_dir: Path,
) -> None:
    """*Prepare*: Process-owned OAuth login fails after catalog persistence.
    *Do*: Add an OAuth MCP source with immediate login.
    *Assert*: The command fails cleanly while the source remains persisted.
    """

    # Prepare
    async def fail_login(server, *, on_url) -> None:
        raise MCPOAuthError("login failed")

    monkeypatch.setattr(_mcp_auth, "perform_oauth_login", fail_login)

    # Do
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp("add", "linear", "--url", "https://mcp.linear.app/mcp")

    # Assert
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert captured.out == "Added MCP server `linear`.\n"
    assert "OAuth login failed: login failed" in captured.err
    assert "Run `/mcp login linear`" in captured.err
    assert _persisted_servers(config_dir)[0]["name"] == "linear"


def test_mcp_add_persists_headers_and_timeouts(config_dir: Path) -> None:
    _run_mcp(
        "add",
        "docs",
        "--url",
        "https://mcp.example.com/mcp",
        "--header",
        "X-Tenant=acme",
        "--header",
        "X-Retry=After:30",
        "--startup-timeout-sec",
        "15",
        "--tool-timeout-sec",
        "120",
    )

    assert _persisted_servers(config_dir)[0] == {
        "name": "docs",
        "transport": "streamable-http",
        "url": "https://mcp.example.com/mcp",
        "startup_timeout_sec": 15.0,
        "tool_timeout_sec": 120.0,
        "auth": {
            "type": "static",
            "headers": {"X-Tenant": "acme", "X-Retry": "After:30"},
        },
    }


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (
            ("--api-key-header", "Authorization"),
            "--api-key-header requires --api-key-env",
        ),
        (("--api-key-format", "Token"), "--api-key-format requires --api-key-env"),
        (("--api-key-env", "TOKEN", "--no-login"), "OAuth options cannot be combined"),
        (
            ("--api-key-env", "TOKEN", "--api-key-format", "Bearer token"),
            "must contain the `{token}` placeholder",
        ),
        (
            ("--header", "authorization=fixed", "--api-key-env", "TOKEN"),
            "--header cannot also define API key header 'Authorization'",
        ),
        (
            ("--header", "X-Tenant=a", "--header", "X-Tenant=b"),
            "Duplicate --header name 'X-Tenant'",
        ),
        (
            ("--header", "X-Tenant=a", "--header", "x-tenant=b"),
            "Duplicate --header name 'x-tenant'",
        ),
    ],
)
def test_mcp_add_rejects_invalid_auth_combinations(
    args: tuple[str, ...], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """*Prepare*: Mutually incompatible authentication flags.
    *Do*: Add a remote MCP source with those flags.
    *Assert*: The CLI rejects the combination with the expected explanation.
    """
    # Prepare
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp("add", "docs", "--url", "https://mcp.example.com/mcp", *args)

    assert exc_info.value.code == 2
    assert message in capsys.readouterr().err


def test_mcp_add_rejects_name_that_normalizes_to_empty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp("add", "!!!", "--url", "https://mcp.example.com/mcp", "--no-login")

    assert exc_info.value.code == 2
    assert "name must contain letters or numbers" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("url", "message"),
    [
        ("https://mcp.example.com:abc/mcp", "valid HTTP(S) URL"),
        ("https://user:secret@mcp.example.com/mcp", "must not include credentials"),
    ],
)
def test_mcp_add_reports_invalid_urls_without_traceback(
    url: str, message: str, capsys: pytest.CaptureFixture[str], config_dir: Path
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp("add", "docs", "--url", url, "--no-login")

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert message in captured.err
    assert "Traceback" not in captured.err
    assert _persisted_servers(config_dir) == []


def test_mcp_add_works_without_provider_api_key(
    monkeypatch: pytest.MonkeyPatch, config_dir: Path
) -> None:
    monkeypatch.delenv("MISTRAL_API_KEY")

    _run_mcp(
        "add",
        "docs",
        "--url",
        "https://mcp.example.com/mcp",
        "--api-key-env",
        "DOCS_API_KEY",
    )

    assert len(_persisted_servers(config_dir)) == 1


def test_mcp_remove_deletes_static_server(
    capsys: pytest.CaptureFixture[str], config_dir: Path
) -> None:
    _run_mcp(
        "add",
        "docs",
        "--url",
        "https://mcp.example.com/mcp",
        "--header",
        "X-Tenant=acme",
    )
    capsys.readouterr()

    _run_mcp("remove", "docs")

    assert capsys.readouterr().out == "Removed MCP server `docs`.\n"
    assert _persisted_servers(config_dir) == []


def test_mcp_remove_is_idempotent_when_server_is_missing(
    capsys: pytest.CaptureFixture[str], config_dir: Path
) -> None:
    _run_mcp("remove", "missing")

    assert capsys.readouterr().out == (
        "MCP server `missing` is not configured in the user config.\n"
    )
    assert _persisted_servers(config_dir) == []


def test_mcp_remove_rejects_name_that_normalizes_to_empty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp("remove", "!!!")

    assert exc_info.value.code == 2
    assert "name must contain letters or numbers" in capsys.readouterr().err


def test_mcp_remove_preserves_unrelated_raw_entries(
    capsys: pytest.CaptureFixture[str], config_dir: Path
) -> None:
    remaining_server: dict[str, object] = {
        "name": "remaining",
        "transport": "streamable-http",
        "url": "https://remaining.example.com/mcp",
        "prompt": "Keep this exact configuration",
        "startup_timeout_sec": 12.0,
        "auth": {"type": "static", "headers": {"X-Tenant": "acme"}},
    }
    _write_persisted_servers(
        config_dir,
        [
            remaining_server,
            {
                "name": "removed",
                "transport": "streamable-http",
                "url": "https://removed.example.com/mcp",
            },
        ],
    )

    _run_mcp("remove", "removed")

    assert capsys.readouterr().out == "Removed MCP server `removed`.\n"
    assert _persisted_servers(config_dir) == [remaining_server]


def test_mcp_remove_cleans_up_oauth_credentials(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    config_dir: Path,
) -> None:
    _run_mcp("add", "linear", "--url", "https://mcp.linear.app/mcp", "--no-login")
    capsys.readouterr()
    cleanup = AsyncMock()
    monkeypatch.setattr(_mcp_auth, "delete_oauth_credentials", cleanup)

    # Do
    _run_mcp("remove", "linear")

    # Assert
    cleanup.assert_awaited_once_with("linear")
    assert capsys.readouterr().out == "Removed MCP server `linear`.\n"
    assert _persisted_servers(config_dir) == []


def test_mcp_remove_keeps_config_after_oauth_cleanup_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    config_dir: Path,
) -> None:
    """*Prepare*: Credential cleanup fails for one persisted OAuth source.
    *Do*: Attempt removal through the sessionless catalog facade.
    *Assert*: The command fails and leaves authoritative config unchanged.
    """
    # Prepare
    _run_mcp("add", "linear", "--url", "https://mcp.linear.app/mcp", "--no-login")
    capsys.readouterr()
    monkeypatch.setattr(
        _mcp_auth,
        "delete_oauth_credentials",
        AsyncMock(side_effect=MCPOAuthError("keyring unavailable")),
    )

    # Do
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp("remove", "linear")

    # Assert
    assert exc_info.value.code == 2
    assert "keyring unavailable" in capsys.readouterr().err
    assert _persisted_servers(config_dir)[0]["name"] == "linear"


def test_mcp_remove_reports_concurrency_conflict_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """*Prepare*: The sessionless catalog removal hits a concurrent config write.
    *Do*: Run the CLI remove command.
    *Assert*: Argparse reports a clean conflict without a traceback.
    """

    # Prepare
    async def conflict(*_args: object, **_kwargs: object) -> None:
        raise ConcurrencyConflictError("expected-fp", "actual-fp")

    monkeypatch.setattr(mcp_catalog, "remove_mcp_server", conflict)

    # Do
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp("remove", "linear")

    # Assert
    assert exc_info.value.code == 2
    assert "modified externally" in capsys.readouterr().err


def test_mcp_remove_restores_credentials_after_config_conflict(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    config_dir: Path,
) -> None:
    """*Prepare*: An OAuth source whose config removal conflicts after credential cleanup.
    *Do*: Remove it through the sessionless catalog facade.
    *Assert*: The credential backup is restored and the configured source remains usable.
    """
    # Prepare
    _run_mcp("add", "linear", "--url", "https://mcp.linear.app/mcp", "--no-login")
    capsys.readouterr()
    backup = object()
    snapshot = AsyncMock(return_value=backup)
    cleanup = AsyncMock()
    restore = AsyncMock()

    async def conflict(*_args: object, **_kwargs: object) -> None:
        raise ConcurrencyConflictError("expected-fp", "actual-fp")

    monkeypatch.setattr(_mcp_auth, "snapshot_oauth_credentials", snapshot)
    monkeypatch.setattr(_mcp_auth, "delete_oauth_credentials", cleanup)
    monkeypatch.setattr(_mcp_auth, "restore_oauth_credentials", restore)
    monkeypatch.setattr(mcp_catalog, "remove_mcp_server", conflict)

    # Do
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp("remove", "linear")

    # Assert
    assert exc_info.value.code == 2
    assert "modified externally" in capsys.readouterr().err
    snapshot.assert_awaited_once_with("linear")
    cleanup.assert_awaited_once_with("linear")
    restore.assert_awaited_once_with("linear", backup)
    assert _persisted_servers(config_dir)[0]["name"] == "linear"


def test_mcp_remove_targets_user_config_when_project_config_exists(
    capsys: pytest.CaptureFixture[str], config_dir: Path, tmp_working_directory: Path
) -> None:
    user_server: dict[str, object] = {
        "name": "docs",
        "transport": "streamable-http",
        "url": "https://user.example.com/mcp",
    }
    project_server: dict[str, object] = {
        "name": "docs",
        "transport": "streamable-http",
        "url": "https://project.example.com/mcp",
    }
    _write_persisted_servers(config_dir, [user_server])
    project_config = tmp_working_directory / ".vibe" / "config.toml"
    project_config.parent.mkdir()
    project_config.write_bytes(
        tomli_w.dumps({"mcp_servers": [project_server]}).encode()
    )
    trusted_folders_manager.add_trusted(project_config.parent)

    _run_mcp("remove", "docs")

    assert capsys.readouterr().out == "Removed MCP server `docs`.\n"
    assert _persisted_servers(config_dir) == []
    with project_config.open("rb") as file:
        assert tomllib.load(file)["mcp_servers"] == [project_server]


def test_entrypoint_routes_mcp_command(monkeypatch: pytest.MonkeyPatch) -> None:
    forwarded_args: list[str] = []
    monkeypatch.setattr(sys, "argv", ["vibe", "mcp", "add", "docs"])
    monkeypatch.setattr(
        mcp_cli, "run_mcp_cli", lambda args: forwarded_args.extend(args)
    )

    entrypoint.main()

    assert forwarded_args == ["add", "docs"]


def test_mcp_help_advertises_add_and_remove(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        _run_mcp("--help")

    assert exc_info.value.code == 0
    output = capsys.readouterr().out
    assert "add" in output
    assert "remove" in output


def test_help_advertises_mcp_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["vibe", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        entrypoint.parse_arguments()

    assert exc_info.value.code == 0
    assert "vibe mcp --help" in capsys.readouterr().out
