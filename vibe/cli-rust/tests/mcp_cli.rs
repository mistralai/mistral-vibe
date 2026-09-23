//! MCP argv parsing and compatibility with the interactive add arguments.

use clap::{error::ErrorKind, CommandFactory, Parser};
use vibe_rs::cli::{Cli, CliCommand};
use vibe_rs::mcp::add_args::{parse_add_args, AddArgs};
use vibe_rs::mcp_command::McpCommand;

fn add_args(args: &[&str]) -> AddArgs {
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
    args
}

#[test]
fn cli_definition_is_consistent() {
    Cli::command().debug_assert();
}

#[test]
fn add_defaults_match_slash_command() {
    let args = add_args(&["https://example.com/mcp"]);
    let slash = parse_add_args("https://example.com/mcp").unwrap();
    assert_eq!(args.url, slash.url);
    assert_eq!(args.name, slash.name);
    assert_eq!(args.scopes, slash.scopes);
    assert_eq!(args.transport, slash.transport);
    assert_eq!(args.login, slash.login);
    assert_eq!(args.allow_insecure_http, slash.allow_insecure_http);
}

#[test]
fn add_flags_preserve_argv_boundaries() {
    let args = add_args(&[
        "http://lan.test/mcp",
        "--name",
        "my server",
        "--scope",
        "read write",
        "--scope=admin",
        "--transport=http",
        "--no-login",
        "--allow-insecure-http",
    ]);
    assert_eq!(args.url, "http://lan.test/mcp");
    assert_eq!(args.name.as_deref(), Some("my server"));
    assert_eq!(args.scopes, ["read write", "admin"]);
    assert_eq!(args.transport, "http");
    assert!(!args.login);
    assert!(args.allow_insecure_http);
}

#[test]
fn remove_requires_exactly_one_name() {
    let cli = Cli::try_parse_from(["vibe", "mcp", "remove", "my server"]).unwrap();
    assert!(matches!(cli.command, Some(CliCommand::Mcp {
        command: McpCommand::Remove { name },
    }) if name == "my server"));
    assert!(Cli::try_parse_from(["vibe", "mcp", "remove"]).is_err());
    assert!(Cli::try_parse_from(["vibe", "mcp", "remove", "one", "two"]).is_err());
}

#[test]
fn invalid_add_arguments_fail_before_startup() {
    for args in [
        vec![],
        vec!["https://example.com", "--transport", "stdio"],
        vec!["https://example.com", "--scope"],
        vec!["https://example.com", "--name", "one", "--name", "two"],
        vec!["https://example.com", "--unknown"],
    ] {
        assert!(Cli::try_parse_from(["vibe", "mcp", "add"].into_iter().chain(args)).is_err());
    }
}

#[test]
fn help_uses_public_command_name() {
    for args in [
        vec!["vibe-rs", "--help"],
        vec!["vibe-rs", "mcp", "--help"],
        vec!["vibe-rs", "mcp", "add", "--help"],
        vec!["vibe-rs", "mcp", "remove", "--help"],
    ] {
        let error = Cli::try_parse_from(args).unwrap_err();
        assert_eq!(error.kind(), ErrorKind::DisplayHelp);
        let help = error.to_string();
        assert!(help.contains("Usage: vibe "));
        assert!(!help.contains("vibe-rs"));
    }
}

#[test]
fn argument_errors_use_public_command_name() {
    let error = Cli::try_parse_from(["/installed/bin/vibe-rs", "mcp", "remove"])
        .unwrap_err()
        .to_string();
    assert!(error.contains("Usage: vibe mcp remove <NAME>"));
    assert!(!error.contains("vibe-rs"));
}

#[test]
fn interactive_and_headless_prompts_are_not_subcommands() {
    let cli = Cli::try_parse_from(["vibe", "mcp add a server"]).unwrap();
    assert!(cli.command.is_none());
    assert_eq!(cli.initial_prompt.as_deref(), Some("mcp add a server"));
    let cli = Cli::try_parse_from(["vibe", "-p", "mcp"]).unwrap();
    assert!(cli.command.is_none());
    assert_eq!(cli.resolve_prompt(None).as_deref(), Some("mcp"));
}
