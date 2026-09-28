"""Sessionless MCP argv scenarios, exercised without a terminal by test_mcp_cli."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MCPArgvScenario:
    name: str
    args: tuple[str, ...]
    responses: dict[str, object]
    requests: tuple[dict[str, object], ...]
    stdout: str
    returncode: int = 0
    stderr_contains: str = ""


_ADD = {
    "method": "mcp_catalog/add",
    "params": {
        "url": "https://example.invalid/mcp",
        "name": None,
        "scopes": [],
        "transport": "streamable-http",
        "allowInsecureHttp": False,
    },
}
_ADDED = {"name": "example", "created": True}
_REMOVE = {"method": "mcp_catalog/remove", "params": {"name": "example"}}
_LOGIN = {"method": "mcp_catalog/login", "params": {"name": "example"}}

SCENARIOS = (
    MCPArgvScenario(
        name="add_no_login",
        args=("add", "https://example.invalid/mcp", "--no-login"),
        responses={"mcp_catalog/add": _ADDED},
        requests=(_ADD,),
        stdout="Added MCP server `example`.\nRun `/mcp login example` to authenticate.\n",
    ),
    MCPArgvScenario(
        name="add_existing",
        args=("add", "https://example.invalid/mcp", "--no-login"),
        responses={"mcp_catalog/add": {"name": "example", "created": False}},
        requests=(_ADD,),
        stdout="MCP server `example` is already configured.\nRun `/mcp login example` to authenticate.\n",
    ),
    MCPArgvScenario(
        name="add_flags",
        args=(
            "add",
            "http://lan.test/mcp",
            "--name",
            "my server",
            "--scope",
            "read write",
            "--scope",
            "admin",
            "--transport",
            "http",
            "--allow-insecure-http",
            "--no-login",
        ),
        responses={"mcp_catalog/add": _ADDED},
        requests=(
            {
                "method": "mcp_catalog/add",
                "params": {
                    "url": "http://lan.test/mcp",
                    "name": "my server",
                    "scopes": ["read write", "admin"],
                    "transport": "http",
                    "allowInsecureHttp": True,
                },
            },
        ),
        stdout="Added MCP server `example`.\nRun `/mcp login example` to authenticate.\n",
    ),
    MCPArgvScenario(
        name="remove",
        args=("remove", "example"),
        responses={"mcp_catalog/remove": {"name": "example", "removed": True}},
        requests=(_REMOVE,),
        stdout="Removed MCP server `example`.\n",
    ),
    MCPArgvScenario(
        name="remove_missing",
        args=("remove", "example"),
        responses={"mcp_catalog/remove": {"name": "example", "removed": False}},
        requests=(_REMOVE,),
        stdout="MCP server `example` is not configured in the user config.\n",
    ),
    MCPArgvScenario(
        name="login",
        args=("add", "https://example.invalid/mcp"),
        responses={"mcp_catalog/add": _ADDED, "mcp_catalog/login": {}},
        requests=(_ADD, _LOGIN),
        stdout="Added MCP server `example`.\nOAuth login completed.\n",
    ),
    MCPArgvScenario(
        name="login_failure",
        args=("add", "https://example.invalid/mcp"),
        responses={"mcp_catalog/add": _ADDED},
        requests=(_ADD, _LOGIN),
        stdout="Added MCP server `example`.\n",
        returncode=1,
        stderr_contains="Run `/mcp login example` to authenticate.",
    ),
    MCPArgvScenario(
        name="add_failure",
        args=("add", "https://example.invalid/mcp"),
        responses={},
        requests=(_ADD,),
        stdout="",
        returncode=1,
        stderr_contains="mcp_catalog/add failed",
    ),
    MCPArgvScenario(
        name="remove_failure",
        args=("remove", "example"),
        responses={},
        requests=(_REMOVE,),
        stdout="",
        returncode=1,
        stderr_contains="mcp_catalog/remove failed",
    ),
)
