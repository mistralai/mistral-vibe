//! `--system-prompt-file`: read, validated, and sent in `agentConfig`.
use clap::Parser;
use serde_json::Value;
use vibe_rs::cli::Cli;
use vibe_rs::headless::agent_config;

fn system_prompt_sent(args: &[&str]) -> Option<String> {
    let options = Cli::parse_from(args).headless_options(None).unwrap();
    let json = serde_json::to_value(agent_config(&options, None, None).unwrap()).unwrap();
    json.get("systemPrompt")
        .and_then(Value::as_str)
        .map(str::to_owned)
}

#[test]
fn the_file_is_sent_as_the_system_prompt() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("system.md");
    std::fs::write(&path, "\n  Work alone.  \n").unwrap();

    let sent = system_prompt_sent(&[
        "vibe-rs",
        "-p",
        "hi",
        "--system-prompt-file",
        path.to_str().unwrap(),
    ]);

    assert_eq!(sent.as_deref(), Some("Work alone."));
}

#[test]
fn without_the_flag_no_system_prompt_is_sent() {
    assert_eq!(system_prompt_sent(&["vibe-rs", "-p", "hi"]), None);
}

#[test]
fn an_empty_file_is_a_usage_error() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("system.md");
    std::fs::write(&path, " \n").unwrap();
    let cli = Cli::parse_from([
        "vibe-rs",
        "-p",
        "hi",
        "--system-prompt-file",
        path.to_str().unwrap(),
    ]);

    let err = cli.headless_options(None).unwrap_err();

    assert_eq!(
        err,
        format!("The system prompt file {} is empty", path.display())
    );
}

#[test]
fn a_missing_file_is_a_usage_error() {
    let cli = Cli::parse_from([
        "vibe-rs",
        "-p",
        "hi",
        "--system-prompt-file",
        "/nonexistent/system.md",
    ]);

    let err = cli.headless_options(None).unwrap_err();

    assert!(
        err.starts_with("Cannot read the system prompt file /nonexistent/system.md: "),
        "{err}"
    );
}

#[test]
fn a_relative_path_resolves_against_the_invocation_directory() {
    let mut cli = Cli::parse_from(["vibe-rs", "-p", "hi", "--system-prompt-file", "system.md"]);

    cli.resolve_headless_paths(std::path::Path::new("/launch"));

    assert_eq!(
        cli.system_prompt_file.as_deref(),
        Some(std::path::Path::new("/launch/system.md"))
    );
}
