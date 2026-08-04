from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import sys
import webbrowser

from pydantic import ValidationError

from vibe.core.auth import MCPOAuthError
from vibe.core.config import (
    MCPHttp,
    MCPOAuth,
    MCPStaticAuth,
    MCPStdio,
    MCPStreamableHttp,
    build_user_config_orchestrator,
)
from vibe.core.config.harness_files import (
    get_harness_files_manager,
    init_harness_files_manager,
)
from vibe.core.config.mcp_servers import (
    MCPServerAddError,
    MCPServerRemoveError,
    PersistedMCPServerResult,
    persist_stdio_mcp_server,
)
from vibe.core.config.types import ConcurrencyConflictError
from vibe.core.tools.mcp.management import (
    add_mcp_server,
    remove_mcp_server_and_credentials,
)


class MCPCommandError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class _MCPAddCommand:
    server: MCPHttp | MCPStreamableHttp | MCPStdio
    login: bool


def run_mcp_cli(argv: list[str]) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.mcp_command is None:
        parser.print_help()
        return

    _ensure_harness_files_manager()
    try:
        match args.mcp_command:
            case "add":
                message = asyncio.run(_add_mcp_server(_parse_add_args(args)))
            case "remove":
                message = asyncio.run(_remove_mcp_server(args.name))
            case _:
                raise MCPCommandError(f"Unsupported MCP command {args.mcp_command!r}.")
    except (
        MCPCommandError,
        MCPServerAddError,
        MCPServerRemoveError,
        ConcurrencyConflictError,
    ) as exc:
        parser.error(str(exc))
    print(message)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vibe mcp", description="Manage MCP server configuration."
    )
    subparsers = parser.add_subparsers(dest="mcp_command")
    add_parser = subparsers.add_parser(
        "add", help="Add an MCP server to the user configuration."
    )
    add_parser.add_argument("name", metavar="NAME", help="MCP server name.")
    add_parser.add_argument(
        "--transport",
        choices=["http", "streamable-http", "stdio"],
        default="streamable-http",
        help="Transport (default: streamable-http).",
    )
    add_parser.add_argument(
        "--url", help="Remote MCP server URL (http and streamable-http transports)."
    )
    add_parser.add_argument(
        "--command", help="Executable to launch the server process (stdio transport)."
    )
    add_parser.add_argument(
        "--arg",
        action="append",
        default=[],
        metavar="VALUE",
        help="Argument for the stdio command. Can be specified multiple times.",
    )
    add_parser.add_argument(
        "--env",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Environment variable for the stdio process. Can be specified "
        "multiple times.",
    )
    add_parser.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "Static HTTP header. Can be specified multiple times. Values are stored "
            "in plaintext in config.toml; use --api-key-env for tokens or secrets."
        ),
    )
    add_parser.add_argument(
        "--api-key-env",
        "--bearer-token-env-var",
        dest="api_key_env",
        metavar="VAR",
        help="Environment variable containing the API key.",
    )
    add_parser.add_argument(
        "--api-key-header",
        metavar="HEADER",
        help="Header carrying the API key (default: Authorization).",
    )
    add_parser.add_argument(
        "--api-key-format",
        metavar="FORMAT",
        help="Header value format containing {token} (default: Bearer {token}).",
    )
    add_parser.add_argument(
        "--no-login",
        action="store_true",
        help="Persist an OAuth server without starting browser login.",
    )
    add_parser.add_argument(
        "--startup-timeout-sec",
        type=float,
        metavar="SECONDS",
        help="Server startup timeout in seconds.",
    )
    add_parser.add_argument(
        "--tool-timeout-sec",
        type=float,
        metavar="SECONDS",
        help="Tool execution timeout in seconds.",
    )
    remove_parser = subparsers.add_parser(
        "remove", help="Remove an MCP server from the user configuration."
    )
    remove_parser.add_argument("name", metavar="NAME", help="MCP server name.")
    return parser


def _parse_add_args(args: argparse.Namespace) -> _MCPAddCommand:
    if args.transport == "stdio":
        return _MCPAddCommand(server=_build_stdio_server(args), login=False)
    return _build_remote_add_command(args)


def _build_remote_add_command(args: argparse.Namespace) -> _MCPAddCommand:
    stdio_only = [
        flag
        for flag, value in (
            ("--command", args.command),
            ("--arg", args.arg),
            ("--env", args.env),
        )
        if value
    ]
    if stdio_only:
        raise MCPCommandError(
            f"{', '.join(stdio_only)} can only be used with --transport stdio."
        )
    if not args.url:
        raise MCPCommandError(
            "--url is required for http and streamable-http transports."
        )

    headers = _parse_headers(tuple(args.header))
    static_auth_requested = bool(
        headers or args.api_key_env or args.api_key_header or args.api_key_format
    )
    if static_auth_requested and args.no_login:
        raise MCPCommandError(
            "OAuth options cannot be combined with static authentication options."
        )

    auth: MCPStaticAuth | MCPOAuth
    if static_auth_requested:
        auth = _build_static_auth(
            headers=headers,
            api_key_env=args.api_key_env,
            api_key_header=args.api_key_header,
            api_key_format=args.api_key_format,
        )
    else:
        auth = MCPOAuth(type="oauth", scopes=[])

    server = _build_remote_server(args, auth)
    return _MCPAddCommand(
        server=server, login=isinstance(auth, MCPOAuth) and not args.no_login
    )


async def _add_mcp_server(command: _MCPAddCommand) -> str:
    orchestrator = await build_user_config_orchestrator()
    server = command.server
    if isinstance(server, MCPStdio):
        return _add_result_message(await persist_stdio_mcp_server(orchestrator, server))

    async def show_oauth_url(url: str) -> None:
        print(f"Open this URL in your browser:\n\n  {url}")
        try:
            webbrowser.open(url)
        except OSError as exc:
            print(f"Could not open the browser: {exc}", file=sys.stderr)

    try:
        result = await add_mcp_server(
            orchestrator,
            server,
            login=command.login,
            on_oauth_url=show_oauth_url,
            on_persisted=(lambda persisted: print(_add_result_message(persisted)))
            if command.login
            else None,
        )
    except MCPOAuthError as exc:
        # The server is persisted; login is best-effort and can be retried.
        print(
            f"vibe mcp add: OAuth login failed: {exc}\n"
            f"Run `/mcp login {server.name}` to authenticate.",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc

    message = _add_result_message(result)
    persisted = result.server
    if not isinstance(persisted.auth, MCPOAuth):
        return message
    if not command.login:
        return f"{message}\nRun `/mcp login {persisted.name}` to authenticate."
    return "OAuth login completed."


async def _remove_mcp_server(name: str) -> str:
    orchestrator = await build_user_config_orchestrator()
    result = await remove_mcp_server_and_credentials(orchestrator, name)
    if not result.removed:
        return f"MCP server `{result.name}` is not configured in the user config."

    return f"Removed MCP server `{result.name}`."


def _add_result_message(result: PersistedMCPServerResult) -> str:
    if result.created:
        return f"Added MCP server `{result.server.name}`."
    return f"MCP server `{result.server.name}` is already configured."


def _build_remote_server(
    args: argparse.Namespace, auth: MCPStaticAuth | MCPOAuth
) -> MCPHttp | MCPStreamableHttp:
    values: dict[str, object] = {
        "name": args.name,
        "transport": args.transport,
        "url": args.url,
        "auth": auth,
    }
    _apply_timeouts(values, args)
    model = MCPHttp if args.transport == "http" else MCPStreamableHttp
    try:
        return model.model_validate(values)
    except ValidationError as exc:
        raise MCPCommandError(f"Invalid MCP server configuration: {exc}") from exc


def _build_stdio_server(args: argparse.Namespace) -> MCPStdio:
    remote_only = [
        flag
        for flag, value in (
            ("--url", args.url),
            ("--header", args.header),
            ("--api-key-env", args.api_key_env),
            ("--api-key-header", args.api_key_header),
            ("--api-key-format", args.api_key_format),
            ("--no-login", args.no_login),
        )
        if value
    ]
    if remote_only:
        raise MCPCommandError(
            f"{', '.join(remote_only)} cannot be used with --transport stdio."
        )
    if not args.command:
        raise MCPCommandError("--command is required for --transport stdio.")

    values: dict[str, object] = {
        "name": args.name,
        "transport": "stdio",
        "command": args.command,
        "args": list(args.arg),
        "env": _parse_env(tuple(args.env)),
    }
    _apply_timeouts(values, args)
    try:
        return MCPStdio.model_validate(values)
    except ValidationError as exc:
        raise MCPCommandError(f"Invalid MCP server configuration: {exc}") from exc


def _apply_timeouts(values: dict[str, object], args: argparse.Namespace) -> None:
    if args.startup_timeout_sec is not None:
        values["startup_timeout_sec"] = args.startup_timeout_sec
    if args.tool_timeout_sec is not None:
        values["tool_timeout_sec"] = args.tool_timeout_sec


def _build_static_auth(
    *,
    headers: dict[str, str],
    api_key_env: str | None,
    api_key_header: str | None,
    api_key_format: str | None,
) -> MCPStaticAuth:
    if api_key_header and not api_key_env:
        raise MCPCommandError("--api-key-header requires --api-key-env.")
    if api_key_format and not api_key_env:
        raise MCPCommandError("--api-key-format requires --api-key-env.")
    api_key_header_name = api_key_header or "Authorization"
    if api_key_env and any(
        name.lower() == api_key_header_name.lower() for name in headers
    ):
        raise MCPCommandError(
            f"--header cannot also define API key header {api_key_header_name!r}."
        )

    values: dict[str, object] = {"headers": headers}
    if api_key_env:
        values["api_key_env"] = api_key_env
        if api_key_header is not None:
            values["api_key_header"] = api_key_header
        if api_key_format is not None:
            values["api_key_format"] = api_key_format
    try:
        return MCPStaticAuth.model_validate(values)
    except ValidationError as exc:
        raise MCPCommandError(f"Invalid static authentication: {exc}") from exc


def _parse_headers(values: tuple[str, ...]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values:
        name, separator, header_value = value.partition("=")
        name = name.strip()
        if not separator:
            raise MCPCommandError("--header values must use NAME=VALUE.")
        if any(existing.lower() == name.lower() for existing in headers):
            raise MCPCommandError(f"Duplicate --header name {name!r}.")
        headers[name] = header_value.strip()
    return headers


def _parse_env(values: tuple[str, ...]) -> dict[str, str]:
    env: dict[str, str] = {}
    for value in values:
        name, separator, env_value = value.partition("=")
        name = name.strip()
        if not separator:
            raise MCPCommandError("--env values must use NAME=VALUE.")
        if name in env:
            raise MCPCommandError(f"Duplicate --env name {name!r}.")
        env[name] = env_value.strip()
    return env


def _ensure_harness_files_manager() -> None:
    try:
        get_harness_files_manager()
    except RuntimeError:
        init_harness_files_manager("user")


__all__ = ["MCPCommandError", "run_mcp_cli"]
