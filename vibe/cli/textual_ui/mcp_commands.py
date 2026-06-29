from __future__ import annotations

from dataclasses import dataclass
import shlex
from typing import Literal

from vibe.core.tools.mcp_settings import MCPAddTransport, parse_mcp_add_transport

MCPSubcommandName = Literal["add", "login", "logout", "status"]

MCP_ADD_USAGE = (
    "Usage: /mcp add <url> [--name <alias>] [--scope <scope> ...] "
    "[--transport <http|streamable-http>] [--no-login]"
)
MCP_ADD_HELP = f"""{MCP_ADD_USAGE}

OAuth-only shortcut for hosted MCP servers.
Defaults to streamable-http; pass --transport http for servers documented with
HTTP transport.
For API-key/static auth, edit config.toml."""


@dataclass(frozen=True, slots=True)
class MCPSubcommand:
    name: MCPSubcommandName
    args: str


@dataclass(frozen=True, slots=True)
class MCPAddArgs:
    url: str
    name: str | None
    scopes: list[str]
    transport: MCPAddTransport
    login: bool


def parse_mcp_subcommand(raw_args: str) -> MCPSubcommand | None:
    parts = raw_args.strip().split(None, 1)
    if not parts:
        return None

    name = _parse_mcp_subcommand_name(parts[0])
    if name is None:
        return None

    args = parts[1].strip() if len(parts) > 1 else ""
    return MCPSubcommand(name=name, args=args)


def is_mcp_add_help_request(raw_args: str) -> bool:
    return raw_args.strip() in {"--help", "-h"}


def parse_mcp_add_args(raw_args: str) -> MCPAddArgs:
    try:
        tokens = shlex.split(raw_args)
    except ValueError as exc:
        raise ValueError(f"Invalid /mcp add arguments: {exc}") from exc

    url: str | None = None
    name: str | None = None
    scopes: list[str] = []
    transport: MCPAddTransport = "streamable-http"
    transport_seen = False
    login = True
    index = 0
    while index < len(tokens):
        token = tokens[index]
        match token:
            case "--no-login":
                login = False
                index += 1
            case "--name":
                if name is not None:
                    raise ValueError("Usage: /mcp add accepts --name only once.")
                name = _mcp_add_option_value(tokens, index, "--name", "<alias>")
                index += 2
            case "--transport":
                if transport_seen:
                    raise ValueError("Usage: /mcp add accepts --transport only once.")
                transport = parse_mcp_add_transport(
                    _mcp_add_option_value(
                        tokens, index, "--transport", "<http|streamable-http>"
                    )
                )
                transport_seen = True
                index += 2
            case "--scope":
                scopes.append(
                    _mcp_add_option_value(tokens, index, "--scope", "<scope>")
                )
                index += 2
            case _ if token.startswith("--"):
                raise ValueError(f"Unknown /mcp add option: {token}")
            case _:
                if url is not None:
                    raise ValueError(MCP_ADD_USAGE)
                url = token
                index += 1

    if url is None:
        raise ValueError(MCP_ADD_USAGE)

    return MCPAddArgs(
        url=url, name=name, scopes=scopes, transport=transport, login=login
    )


def _parse_mcp_subcommand_name(value: str) -> MCPSubcommandName | None:
    match value:
        case "add" | "login" | "logout" | "status":
            return value
        case _:
            return None


def _mcp_add_option_value(
    tokens: list[str], index: int, option: str, placeholder: str
) -> str:
    if index + 1 >= len(tokens) or tokens[index + 1].startswith("--"):
        raise ValueError(f"Usage: /mcp add {option} {placeholder}")
    return tokens[index + 1]
