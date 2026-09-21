//! Interactive-mode session flags, mirroring the Python interactive
//! `SessionOptions` (cli.py) built from the shared CLI flags.

use clap::Parser;
use vibe_rs::cli::Cli;

fn config_from(args: &[&str]) -> vibe_rs::server::AgentConfig {
    let cli = Cli::parse_from(args);
    cli.interactive_agent_config(Some("/work".into())).unwrap()
}

#[test]
fn interactive_defaults_match_python() {
    let config = config_from(&["vibe-rs"]);
    assert_eq!(config.cwd.as_deref(), Some("/work"));
    assert_eq!(config.agent, None);
    assert!(!config.auto_approve);
    assert!(config.enabled_tools.is_empty());
    assert!(config.disabled_tools.is_empty());
    // Budget flags stay headless-only, as in Python.
    assert_eq!(config.max_turns, None);
    assert_eq!(config.max_price, None);
    assert_eq!(config.max_session_tokens, None);
    assert!(!config.headless);
    assert!(!config.trust_workspace);
    assert!(config.workspace_roots.is_empty());
}

#[test]
fn interactive_yolo_without_agent_is_a_session_wide_bypass() {
    let config = config_from(&["vibe-rs", "--yolo"]);
    assert_eq!(config.agent, None);
    assert!(config.auto_approve);
}

#[test]
fn interactive_smart_approve_selects_agent_when_no_explicit_agent() {
    let config = config_from(&["vibe-rs", "--smart-approve"]);
    assert_eq!(config.agent.as_deref(), Some("smart-approve"));
    assert!(!config.auto_approve);
}

#[test]
fn interactive_yolo_with_agent_keeps_flag() {
    let config = config_from(&["vibe-rs", "--agent", "plan", "--yolo"]);
    assert_eq!(config.agent.as_deref(), Some("plan"));
    assert!(config.auto_approve);
}

#[test]
fn interactive_tool_filters_and_trust_are_wired() {
    // Python uses action="append": repeat the flag; commas stay literal.
    let config = config_from(&[
        "vibe-rs",
        "--enabled-tools",
        "bash",
        "--enabled-tools",
        "edit",
        "--disabled-tools",
        "web_search",
        "--trust",
    ]);
    assert_eq!(config.enabled_tools, ["bash", "edit"]);
    assert_eq!(config.disabled_tools, ["web_search"]);
    assert!(config.trust_workspace);
}

#[test]
fn interactive_add_dir_resolves_to_workspace_roots() {
    let cwd = std::env::current_dir().unwrap();
    let config = Cli::parse_from(["vibe-rs", "--add-dir", "."])
        .interactive_agent_config(Some(cwd.to_string_lossy().into_owned()))
        .unwrap();
    assert_eq!(config.workspace_roots.len(), 1);
    assert!(
        config.workspace_roots[0].ends_with("cli-rust"),
        "expected the canonical cli-rust dir, got {}",
        config.workspace_roots[0]
    );
}

#[test]
fn interactive_add_dir_rejects_missing_paths() {
    let error = Cli::parse_from(["vibe-rs", "--add-dir", "/does/not/exist"])
        .interactive_agent_config(None)
        .unwrap_err();
    assert!(error.to_string().contains("--add-dir"));
}
