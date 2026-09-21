//! `--agent` / `--auto-approve` (`--yolo`) / `--smart-approve` parsing, resolution, and wire serialization.

use clap::Parser;
use serde_json::{json, Value};
use vibe_rs::cli::{Cli, ExecutionControl};
use vibe_rs::server::{AgentConfig, SessionStartParams, HISTORY_LIMIT};

fn control(args: &[&str]) -> ExecutionControl {
    Cli::try_parse_from(std::iter::once("vibe").chain(args.iter().copied()))
        .expect("flags parse")
        .execution_control()
}

#[test]
fn no_flags_resolve_to_defaults() {
    assert_eq!(control(&[]), ExecutionControl::default());
    assert_eq!(control(&[]).agent, None);
    assert!(!control(&[]).auto_approve);
}

#[test]
fn agent_flag_selects_agent() {
    assert_eq!(
        control(&["--agent", "lean"]),
        ExecutionControl {
            agent: Some("lean".into()),
            auto_approve: false,
        }
    );
}

#[test]
fn auto_approve_and_yolo_resolve_identically() {
    let auto = control(&["--auto-approve"]);
    let yolo = control(&["--yolo"]);
    assert_eq!(
        auto,
        ExecutionControl {
            agent: None,
            auto_approve: true,
        }
    );
    assert_eq!(auto, yolo);
}

#[test]
fn smart_approve_selects_agent_when_no_explicit_agent() {
    assert_eq!(
        control(&["--smart-approve"]).agent,
        Some("smart-approve".into())
    );
}

#[test]
fn explicit_agent_wins_over_smart_approve() {
    assert_eq!(
        control(&["--smart-approve", "--agent", "plan"]).agent,
        Some("plan".into())
    );
}

#[test]
fn agent_and_auto_approve_combine() {
    assert_eq!(
        control(&["--agent", "lean", "--yolo"]),
        ExecutionControl {
            agent: Some("lean".into()),
            auto_approve: true,
        }
    );
}

#[test]
fn exec_control_flags_coexist_with_resume_flags() {
    let cli = Cli::try_parse_from(["vibe", "--agent", "lean", "--continue"])
        .expect("flags parse together");
    assert!(cli.continue_session);
    assert_eq!(cli.execution_control().agent, Some("lean".into()));
}

#[test]
fn default_agent_config_omits_exec_control_keys() {
    let value: Value = serde_json::to_value(AgentConfig::default()).unwrap();
    assert!(value.get("agent").is_none());
    assert!(value.get("autoApprove").is_none());
}

#[test]
fn agent_config_serializes_exec_control_camel_case() {
    let value: Value = serde_json::to_value(AgentConfig {
        cwd: Some("/x".into()),
        agent: Some("smart-approve".into()),
        auto_approve: true,
        ..Default::default()
    })
    .unwrap();
    assert_eq!(
        value,
        json!({"cwd": "/x", "agent": "smart-approve", "autoApprove": true})
    );
}

#[test]
fn agent_config_omits_auto_approve_when_false() {
    let value: Value = serde_json::to_value(AgentConfig {
        cwd: None,
        agent: Some("plan".into()),
        auto_approve: false,
        ..Default::default()
    })
    .unwrap();
    assert_eq!(value, json!({"agent": "plan"}));
}

#[test]
fn session_start_params_carry_resolved_execution_control() {
    let execution_control = control(&["--smart-approve", "--yolo"]);
    let params = SessionStartParams {
        agent_config: AgentConfig {
            cwd: Some("/work".into()),
            agent: execution_control.agent.clone(),
            auto_approve: execution_control.auto_approve,
            ..Default::default()
        },
        history_limit: HISTORY_LIMIT,
    };
    let value: Value = serde_json::to_value(&params).unwrap();
    assert_eq!(value["agentConfig"]["agent"], "smart-approve");
    assert_eq!(value["agentConfig"]["autoApprove"], true);
    assert_eq!(value["historyLimit"], 200);
}

#[test]
fn session_start_params_without_flags_match_default_payload() {
    let params = SessionStartParams {
        agent_config: AgentConfig {
            cwd: Some("/work".into()),
            agent: None,
            auto_approve: false,
            ..Default::default()
        },
        history_limit: HISTORY_LIMIT,
    };
    let value: Value = serde_json::to_value(&params).unwrap();
    assert_eq!(value["agentConfig"], json!({"cwd": "/work"}));
}

#[test]
fn smart_approve_resolves_agent_in_headless_options() {
    let cli = Cli::parse_from(["vibe", "-p", "hi", "--smart-approve"]);
    let options = cli.headless_options(None).unwrap();
    assert_eq!(options.agent.as_deref(), Some("smart-approve"));
    assert!(!options.auto_approve);
}

#[test]
fn headless_yolo_without_agent_is_a_session_wide_bypass() {
    let cli = Cli::parse_from(["vibe", "-p", "hi", "--yolo"]);
    let options = cli.headless_options(None).unwrap();
    let config = vibe_rs::headless::agent_config(&options, Some("/work".into())).unwrap();
    assert!(config.agent.is_none());
    assert!(config.auto_approve);
    let params = SessionStartParams {
        agent_config: config,
        history_limit: HISTORY_LIMIT,
    };
    let value: Value = serde_json::to_value(&params).unwrap();
    assert!(value["agentConfig"].get("agent").is_none());
    assert_eq!(value["agentConfig"]["autoApprove"], true);
}
