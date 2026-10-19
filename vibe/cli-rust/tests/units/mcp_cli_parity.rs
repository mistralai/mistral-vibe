//! `vibe mcp add` arguments: the canonical syntax and the legacy invocations.

use clap::Parser;
use serde_json::json;
use vibe_rs::cli::{Cli, CliCommand};
use vibe_rs::mcp_command::{add_args::McpAddArgs, McpCommand};

fn parse(args: &[&str]) -> McpAddArgs {
    let cli = Cli::try_parse_from(
        ["vibe", "mcp", "add"]
            .into_iter()
            .chain(args.iter().copied()),
    )
    .unwrap();
    let Some(CliCommand::Mcp {
        command: McpCommand::Add(args),
    }) = cli.command
    else {
        panic!("expected MCP add");
    };
    *args
}

#[test]
fn canonical_mistral_command_uses_static_auth() {
    for flag in ["--api-key-env", "--bearer-token-env-var"] {
        let args = parse(&[
            "mistralai",
            "--url",
            "https://api.mistral.ai/mcp",
            "--transport",
            "streamable-http",
            flag,
            "MISTRAL_API_KEY",
        ]);
        assert!(!args.is_oauth());
        assert_eq!(
            args.params().unwrap(),
            json!({
                "name": "mistralai",
                "url": "https://api.mistral.ai/mcp",
                "transport": "streamable-http",
                "scopes": [],
                "allowInsecureHttp": false,
                "apiKeyEnv": "MISTRAL_API_KEY",
            })
        );
    }
}

#[test]
fn canonical_and_legacy_oauth_arguments_produce_the_same_request() {
    let canonical = parse(&["docs", "--url", "https://example.com/mcp", "--no-login"]);
    let legacy = parse(&["https://example.com/mcp", "--name", "docs", "--no-login"]);
    assert!(canonical.is_oauth());
    assert!(canonical.no_login);
    assert_eq!(canonical.params().unwrap(), legacy.params().unwrap());
}

#[test]
fn headers_auth_and_timeouts_reach_the_server_unchanged() {
    let args = parse(&[
        "docs",
        "--url",
        "https://example.com/mcp",
        "--header",
        " X-Workspace = team one ",
        "--header",
        "X-Mode=a=b",
        "--api-key-env",
        "DOCS_KEY",
        "--api-key-header",
        "X-Token",
        "--api-key-format",
        "Token {token}",
        "--startup-timeout-sec",
        "12.5",
        "--tool-timeout-sec",
        "120",
    ]);
    let params = args.params().unwrap();
    assert!(!args.is_oauth());
    assert_eq!(
        params["headers"],
        json!({"X-Workspace": "team one", "X-Mode": "a=b"})
    );
    assert_eq!(params["apiKeyHeader"], "X-Token");
    assert_eq!(params["apiKeyFormat"], "Token {token}");
    assert_eq!(params["startupTimeoutSec"], 12.5);
    assert_eq!(params["toolTimeoutSec"], 120.0);
}

#[test]
fn header_only_auth_does_not_login() {
    let args = parse(&[
        "docs",
        "--url",
        "https://example.com",
        "--header",
        "X-Token=local",
    ]);
    assert!(!args.is_oauth());
}

#[test]
fn stdio_preserves_command_arguments_and_environment() {
    let args = parse(&[
        "files",
        "--transport",
        "stdio",
        "--command",
        "npx",
        "--arg=-y",
        "--arg",
        "@modelcontextprotocol/server-filesystem",
        "--arg",
        "some folder",
        "--env",
        "ROOT= some folder ",
        "--env",
        "MODE=read",
        "--tool-timeout-sec",
        "30",
    ]);
    assert!(!args.is_oauth());
    assert_eq!(
        args.params().unwrap(),
        json!({
            "name": "files", "url": null, "transport": "stdio",
            "scopes": [], "allowInsecureHttp": false,
            "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "some folder"],
            "env": {"ROOT": " some folder ", "MODE": "read"}, "toolTimeoutSec": 30.0,
        })
    );
}

#[test]
fn conflicting_or_unserializable_options_fail_before_startup() {
    for extra in [
        vec!["--no-login", "--api-key-env", "KEY"],
        vec!["--no-login", "--header", "X-Key=value"],
        vec!["--scope", "read", "--api-key-env", "KEY"],
        vec!["--api-key-header", "X-Key"],
        vec!["--api-key-format", "Token {token}"],
        vec!["--url", "https://example.com", "--name", "docs"],
        vec!["--transport", "stdio"],
        vec!["--startup-timeout-sec", "NaN"],
        vec!["--tool-timeout-sec", "inf"],
    ] {
        assert!(
            Cli::try_parse_from(["vibe", "mcp", "add", "docs"].into_iter().chain(extra)).is_err()
        );
    }
}

#[test]
fn name_without_url_requires_url_for_remote_transports() {
    for extra in [
        vec![],
        vec!["--api-key-env", "KEY"],
        vec!["--header", "X-Key=value"],
        vec!["--transport", "http"],
    ] {
        let argv: Vec<&str> = ["docs"].into_iter().chain(extra).collect();
        assert_eq!(
            parse(&argv).params().unwrap_err().to_string(),
            "--url is required for http and streamable-http transports."
        );
    }
}

#[test]
fn malformed_or_duplicate_assignments_fail_without_echoing_values() {
    const REMOTE: [&str; 3] = ["docs", "--url", "https://example.com"];
    const STDIO: [&str; 5] = ["docs", "--transport", "stdio", "--command", "uvx"];
    for (base, extra, error) in [
        (
            &REMOTE[..],
            vec!["--header", "Authorization: Bearer secret"],
            "--header values must use NAME=VALUE.",
        ),
        (
            &REMOTE[..],
            vec!["--header", "X-A=secret", "--header", " X-A =other"],
            "Duplicate --header name 'X-A'.",
        ),
        (
            &STDIO[..],
            vec!["--env", "secret"],
            "--env values must use NAME=VALUE.",
        ),
        (
            &STDIO[..],
            vec!["--env", "A=secret", "--env", "A=other"],
            "Duplicate --env name 'A'.",
        ),
    ] {
        let argv: Vec<&str> = base.iter().copied().chain(extra).collect();
        let message = parse(&argv).params().unwrap_err().to_string();
        assert_eq!(message, error);
        assert!(!message.contains("secret"));
    }
}
