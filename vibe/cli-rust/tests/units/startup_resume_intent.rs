//! `--continue`/`--resume` CLI intent resolution.

use clap::Parser;
use serde_json::json;
use vibe_rs::cli::{Cli, StartupResume};
use vibe_rs::server::{AgentConfig, RequestFailure, RpcError, WorktreeInput, HISTORY_LIMIT};
use vibe_rs::startup::{continue_params, nothing_to_continue, resume_params};

#[test]
fn no_flags_is_no_intent() {
    let cli = Cli::parse_from(["vibe-rs"]);
    assert_eq!(cli.startup_resume(), StartupResume::None);
}

#[test]
fn continue_flag_is_continue_intent() {
    let cli = Cli::parse_from(["vibe-rs", "--continue"]);
    assert_eq!(cli.startup_resume(), StartupResume::Continue);
}

#[test]
fn short_continue_flag_is_continue_intent() {
    let cli = Cli::parse_from(["vibe-rs", "-c"]);
    assert_eq!(cli.startup_resume(), StartupResume::Continue);
}

#[test]
fn resume_with_id_is_by_id_intent() {
    let cli = Cli::parse_from(["vibe-rs", "--resume", "abc123"]);
    assert_eq!(cli.startup_resume(), StartupResume::ById("abc123".into()));
}

#[test]
fn resume_without_id_is_picker_intent() {
    let cli = Cli::parse_from(["vibe-rs", "--resume"]);
    assert_eq!(cli.startup_resume(), StartupResume::Picker);
}

fn terminal_config() -> AgentConfig {
    AgentConfig {
        cwd: Some("/work".into()),
        terminal_key: Some("/dev/ttys003".into()),
        worktree: Some(WorktreeInput::Auto { prompt: None }),
        ..Default::default()
    }
}

/// The server picks the session, so continue names none and sends the terminal.
#[test]
fn continue_sends_the_terminal_without_a_session_or_worktree() {
    assert_eq!(
        continue_params(&terminal_config()),
        json!({
            "agentConfig": {"cwd": "/work", "terminalKey": "/dev/ttys003"},
            "historyLimit": HISTORY_LIMIT,
        })
    );
}

#[test]
fn resume_sends_the_terminal_without_a_worktree() {
    let params = resume_params("abc", &terminal_config());
    assert_eq!(params["sessionId"], "abc");
    assert_eq!(
        params["agentConfig"],
        json!({"cwd": "/work", "terminalKey": "/dev/ttys003"})
    );
}

fn not_found(data: Option<serde_json::Value>) -> anyhow::Error {
    anyhow::Error::new(RequestFailure::Rpc(RpcError {
        code: json!("not_found"),
        message: "nope".into(),
        data,
    }))
}

/// Only the server's "nothing to continue" starts fresh; other misses, like disabled logging, say why.
#[test]
fn only_nothing_to_continue_counts_as_no_previous_session() {
    let nothing = not_found(Some(json!({"harnessCode": "nothing_to_continue"})));
    assert!(nothing_to_continue(&nothing));
    assert!(!nothing_to_continue(&not_found(None)));
}
