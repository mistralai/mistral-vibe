"""MCP add argv scenarios: canonical names, static auth, stdio, and validation ownership."""

from __future__ import annotations

from scenarios._mcp_argv import MCPArgvScenario

_BASE_PARAMS: dict[str, object] = {
    "url": "https://example.invalid/mcp",
    "name": "example",
    "scopes": [],
    "transport": "streamable-http",
    "allowInsecureHttp": False,
}
_REMOTE = ("example", "--url", "https://example.invalid/mcp")
_STDIO = ("example", "--transport", "stdio", "--command", "missing-mcp-server")


def _add(
    name: str,
    args: tuple[str, ...],
    params: dict[str, object],
    *,
    created: bool = True,
    error: str = "",
) -> MCPArgvScenario:
    expected = {**_BASE_PARAMS, **params}
    server_name = expected["name"]
    response = (
        {"error": {"code": "invalid_params", "message": error}}
        if error
        else {"name": server_name, "created": created}
    )
    stdout = (
        f"Added MCP server `{server_name}`.\n"
        if created
        else f"MCP server `{server_name}` is already configured.\n"
    )
    return MCPArgvScenario(
        name=name,
        args=("add", *args),
        responses={"mcp_catalog/add": response},
        requests=({"method": "mcp_catalog/add", "params": expected},),
        stdout="" if error else stdout,
        returncode=1 if error else 0,
        stderr_contains=error,
    )


CANONICAL = _add(
    "add_canonical_mistralai",
    (
        "mistralai",
        "--url",
        "https://api.mistral.ai/mcp",
        "--transport",
        "streamable-http",
        "--api-key-env",
        "MISTRAL_API_KEY",
    ),
    {
        "name": "mistralai",
        "url": "https://api.mistral.ai/mcp",
        "apiKeyEnv": "MISTRAL_API_KEY",
    },
)

ADD_SCENARIOS = (
    CANONICAL,
    _add(
        "add_bearer_token_env_alias",
        (*_REMOTE, "--bearer-token-env-var", "MCP_TOKEN"),
        {"apiKeyEnv": "MCP_TOKEN"},
    ),
    _add(
        "add_legacy_static_auth",
        (
            "https://example.invalid/mcp",
            "--name",
            "example",
            "--api-key-env",
            "MCP_TOKEN",
        ),
        {"apiKeyEnv": "MCP_TOKEN"},
    ),
    _add(
        "add_static_existing_no_login",
        (*_REMOTE, "--api-key-env", "MCP_TOKEN"),
        {"apiKeyEnv": "MCP_TOKEN"},
        created=False,
    ),
    _add(
        "add_headers_only",
        (*_REMOTE, "--header", "X-Tenant=acme team", "--header=X-Query=a=b"),
        {"headers": {"X-Tenant": "acme team", "X-Query": "a=b"}},
    ),
    _add(
        "add_custom_api_key_header_and_format",
        (
            *_REMOTE,
            "--api-key-env",
            "MCP_TOKEN",
            "--api-key-header",
            "X-API-Key",
            "--api-key-format",
            "Token {token}",
            "--header",
            "X-Tenant=acme",
        ),
        {
            "apiKeyEnv": "MCP_TOKEN",
            "apiKeyHeader": "X-API-Key",
            "apiKeyFormat": "Token {token}",
            "headers": {"X-Tenant": "acme"},
        },
    ),
    _add(
        "add_explicit_default_api_key_header_and_format",
        (
            *_REMOTE,
            "--api-key-env",
            "MCP_TOKEN",
            "--api-key-header",
            "Authorization",
            "--api-key-format",
            "Bearer {token}",
        ),
        {
            "apiKeyEnv": "MCP_TOKEN",
            "apiKeyHeader": "Authorization",
            "apiKeyFormat": "Bearer {token}",
        },
    ),
    _add(
        "add_header_only_http_with_timeouts",
        (
            "example",
            "--url",
            "http://lan.test/mcp",
            "--transport",
            "http",
            "--allow-insecure-http",
            "--header",
            "X-Tenant=acme",
            "--startup-timeout-sec",
            "15.5",
            "--tool-timeout-sec",
            "120.25",
        ),
        {
            "url": "http://lan.test/mcp",
            "transport": "http",
            "allowInsecureHttp": True,
            "headers": {"X-Tenant": "acme"},
            "startupTimeoutSec": 15.5,
            "toolTimeoutSec": 120.25,
        },
    ),
    _add(
        "add_stdio_no_login",
        _STDIO,
        {"url": None, "transport": "stdio", "command": "missing-mcp-server"},
    ),
    _add(
        "add_stdio_args_env_and_timeouts",
        (
            *_STDIO,
            "--arg=-y",
            "--arg",
            "package name",
            "--arg=--config=a=b",
            "--env",
            "TENANT=acme team",
            "--env=QUERY=a=b",
            "--env=EMPTY=",
            "--startup-timeout-sec",
            "2.5",
            "--tool-timeout-sec",
            "60.75",
        ),
        {
            "url": None,
            "transport": "stdio",
            "command": "missing-mcp-server",
            "args": ["-y", "package name", "--config=a=b"],
            "env": {"TENANT": "acme team", "QUERY": "a=b", "EMPTY": ""},
            "startupTimeoutSec": 2.5,
            "toolTimeoutSec": 60.75,
        },
    ),
    MCPArgvScenario(
        name="add_canonical_oauth",
        args=("add", *_REMOTE, "--scope", "read write", "--scope=admin"),
        responses={
            "mcp_catalog/add": {"name": "example", "created": True},
            "mcp_catalog/login": {},
        },
        requests=(
            {
                "method": "mcp_catalog/add",
                "params": {**_BASE_PARAMS, "scopes": ["read write", "admin"]},
            },
            {"method": "mcp_catalog/login", "params": {"name": "example"}},
        ),
        stdout="Added MCP server `example`.\nOAuth login completed.\n",
    ),
)

SERVER_ERRORS = (
    *(
        _add(name, (*_REMOTE, *args), params, error=message)
        for name, args, params, message in (
            (
                "add_case_insensitive_duplicate_headers_server_error",
                ("--header", "X-Tenant=a", "--header", "x-tenant=b"),
                {"headers": {"X-Tenant": "a", "x-tenant": "b"}},
                "Duplicate HTTP header 'x-tenant'",
            ),
            (
                "add_api_key_header_collision_server_error",
                ("--header", "authorization=fixed", "--api-key-env", "TOKEN"),
                {"headers": {"authorization": "fixed"}, "apiKeyEnv": "TOKEN"},
                "headers cannot also define API key header 'Authorization'.",
            ),
            (
                "add_invalid_api_key_format_server_error",
                ("--api-key-env", "TOKEN", "--api-key-format", "Bearer token"),
                {"apiKeyEnv": "TOKEN", "apiKeyFormat": "Bearer token"},
                "api_key_format must contain the `{token}` placeholder.",
            ),
            (
                "add_zero_startup_timeout_server_error",
                ("--startup-timeout-sec", "0"),
                {"startupTimeoutSec": 0.0},
                "startup_timeout_sec must be greater than 0.",
            ),
            (
                "add_negative_tool_timeout_server_error",
                ("--tool-timeout-sec=-1",),
                {"toolTimeoutSec": -1.0},
                "tool_timeout_sec must be greater than 0.",
            ),
        )
    ),
    *(
        _add(
            name,
            (*_STDIO, *args),
            {
                "url": None,
                "transport": "stdio",
                "command": "missing-mcp-server",
                **params,
            },
            error=message,
        )
        for name, args, params, message in (
            (
                "add_invalid_env_name_server_error",
                ("--env", "=acme"),
                {"env": {"": "acme"}},
                "env must use valid environment variable names.",
            ),
        )
    ),
)

SERVER_ERRORS += tuple(
    _add(name, args, params, error=message)
    for name, args, params, message in (
        (
            "add_remote_command_server_error",
            (*_REMOTE, "--command", "missing-mcp-server"),
            {"command": "missing-mcp-server"},
            "command, args and env can only be used with stdio.",
        ),
        (
            "add_remote_args_server_error",
            (*_REMOTE, "--arg=value"),
            {"args": ["value"]},
            "command, args and env can only be used with stdio.",
        ),
        (
            "add_remote_env_server_error",
            (*_REMOTE, "--env", "TENANT=acme"),
            {"env": {"TENANT": "acme"}},
            "command, args and env can only be used with stdio.",
        ),
        (
            "add_invalid_url_server_error",
            ("example", "--url", "not-a-url"),
            {"url": "not-a-url"},
            "url must be a valid HTTP(S) URL.",
        ),
        (
            "add_invalid_name_server_error",
            ("!!!", "--url", "https://example.invalid/mcp"),
            {"name": "!!!"},
            "name must contain letters or numbers.",
        ),
        (
            "add_invalid_key_env_server_error",
            (*_REMOTE, "--api-key-env", "invalid-key-env"),
            {"apiKeyEnv": "invalid-key-env"},
            "apiKeyEnv must be a valid environment variable name.",
        ),
    )
)
SERVER_ERRORS += tuple(
    _add(
        name,
        (*_STDIO, *args),
        {"url": None, "transport": "stdio", "command": "missing-mcp-server", **params},
        error="Remote options cannot be used with stdio.",
    )
    for name, args, params in (
        (
            "add_stdio_url_server_error",
            ("--url", "https://example.invalid/mcp"),
            {"url": "https://example.invalid/mcp"},
        ),
        (
            "add_stdio_header_server_error",
            ("--header", "X-Tenant=acme"),
            {"headers": {"X-Tenant": "acme"}},
        ),
        (
            "add_stdio_api_key_server_error",
            ("--api-key-env", "TOKEN"),
            {"apiKeyEnv": "TOKEN"},
        ),
        (
            "add_stdio_insecure_http_server_error",
            ("--allow-insecure-http",),
            {"allowInsecureHttp": True},
        ),
    )
)

SERVER_ERRORS += tuple(
    MCPArgvScenario(
        name=name,
        args=("add", *_REMOTE, *args),
        responses={
            "mcp_catalog/add": {
                "error": {
                    "code": "invalid_params",
                    "message": "Invalid request parameters",
                    "data": {
                        "errorCount": 1,
                        "issues": [{"path": path, "message": message}],
                    },
                }
            }
        },
        requests=({"method": "mcp_catalog/add", "params": {**_BASE_PARAMS, **params}},),
        stdout="",
        returncode=1,
        stderr_contains=f"{'.'.join(path)}: {message}",
    )
    for name, args, params, path, message in (
        (
            "add_zero_startup_timeout_validation_issue",
            ("--startup-timeout-sec", "0"),
            {"startupTimeoutSec": 0.0},
            ["startup_timeout_sec"],
            "Input should be greater than 0",
        ),
        (
            "add_invalid_api_key_format_validation_issue",
            ("--api-key-env", "TOKEN", "--api-key-format", "Bearer token"),
            {"apiKeyEnv": "TOKEN", "apiKeyFormat": "Bearer token"},
            ["auth", "api_key_format"],
            "Value error, api_key_format must contain the `{token}` placeholder.",
        ),
    )
)

LOCAL_ERRORS = tuple(
    MCPArgvScenario(
        name=f"add_rejects_{name}",
        args=("add", *args),
        responses={},
        requests=(),
        stdout="",
        returncode=2,
        stderr_contains=flag,
        starts_server=False,
    )
    for name, args, flag in (
        ("missing_target", (), "Usage:"),
        ("duplicate_name", (*_REMOTE, "--name", "other"), "--name"),
        ("extra_positional", (*_REMOTE, "other"), "other"),
        ("missing_url_value", ("example", "--url"), "--url"),
        ("missing_command", ("example", "--transport", "stdio"), "--command"),
        ("stdio_legacy_name", (*_STDIO, "--name", "other"), "--name"),
        ("stdio_scope", (*_STDIO, "--scope", "read"), "--scope"),
        ("stdio_no_login", (*_STDIO, "--no-login"), "--no-login"),
        (
            "header_without_key",
            (*_REMOTE, "--api-key-header", "X-Key"),
            "--api-key-env",
        ),
        (
            "format_without_key",
            (*_REMOTE, "--api-key-format", "Token {token}"),
            "--api-key-env",
        ),
        (
            "static_no_login",
            (*_REMOTE, "--api-key-env", "TOKEN", "--no-login"),
            "--no-login",
        ),
        (
            "headers_no_login",
            (*_REMOTE, "--header", "X-Tenant=acme", "--no-login"),
            "--no-login",
        ),
        (
            "static_scope",
            (*_REMOTE, "--api-key-env", "TOKEN", "--scope", "read"),
            "--scope",
        ),
        (
            "headers_scope",
            (*_REMOTE, "--header", "X-Tenant=acme", "--scope", "read"),
            "--scope",
        ),
        (
            "non_numeric_timeout",
            (*_REMOTE, "--startup-timeout-sec", "soon"),
            "--startup-timeout-sec",
        ),
        ("nan_timeout", (*_REMOTE, "--startup-timeout-sec", "NaN"), "finite"),
        ("infinite_timeout", (*_REMOTE, "--tool-timeout-sec", "inf"), "finite"),
        ("unknown_option", (*_REMOTE, "--unknown"), "--unknown"),
    )
)

LOCAL_ERRORS += tuple(
    MCPArgvScenario(
        name=f"add_rejects_{name}",
        args=("add", *args),
        responses={},
        requests=(),
        stdout="",
        returncode=1,
        stderr_contains=message,
        starts_server=False,
    )
    for name, args, message in (
        (
            "static_missing_url",
            ("example", "--api-key-env", "TOKEN"),
            "--url is required for http and streamable-http transports.",
        ),
        (
            "malformed_header",
            (*_REMOTE, "--header", "Authorization: Bearer token"),
            "--header values must use NAME=VALUE.",
        ),
        (
            "duplicate_header",
            (*_REMOTE, "--header", "X-Tenant=a", "--header", "X-Tenant=b"),
            "Duplicate --header name 'X-Tenant'.",
        ),
        (
            "malformed_env",
            (*_STDIO, "--env", "TENANT"),
            "--env values must use NAME=VALUE.",
        ),
        (
            "duplicate_env",
            (*_STDIO, "--env", "TENANT=a", "--env", "TENANT=b"),
            "Duplicate --env name 'TENANT'.",
        ),
    )
)
