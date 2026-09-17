//! `session/resume` keeps the interactive flags, mirroring the Python
//! `SessionOptions` sent on both start and resume (cli.py).

use clap::Parser;
use vibe_rs::cli::Cli;
use vibe_rs::server::HISTORY_LIMIT;
use vibe_rs::startup::resume_params;

#[test]
fn resume_carries_interactive_flags() {
    let cli = Cli::parse_from([
        "vibe-rs",
        "--resume",
        "session-1",
        "--agent",
        "plan",
        "--yolo",
        "--trust",
        "--add-dir",
        ".",
        "--enabled-tools",
        "bash",
        "--disabled-tools",
        "web_search",
    ]);
    let config = cli.interactive_agent_config(Some("/work".into())).unwrap();
    let params = resume_params("session-1", &config);

    assert_eq!(params["sessionId"], "session-1");
    assert_eq!(params["historyLimit"], HISTORY_LIMIT);
    assert_eq!(params["agentConfig"]["cwd"], "/work");
    assert_eq!(params["agentConfig"]["agent"], "plan");
    assert_eq!(params["agentConfig"]["autoApprove"], true);
    assert_eq!(
        params["agentConfig"]["enabledTools"],
        serde_json::json!(["bash"])
    );
    assert_eq!(
        params["agentConfig"]["disabledTools"],
        serde_json::json!(["web_search"])
    );
    assert_eq!(params["agentConfig"]["trustWorkspace"], true);
    let cwd = std::path::Path::new(".").canonicalize().unwrap();
    assert_eq!(
        params["agentConfig"]["workspaceRoots"],
        serde_json::json!([cwd.to_str().unwrap()])
    );
}

#[test]
fn resume_defaults_stay_slim() {
    let cli = Cli::parse_from(["vibe-rs", "--resume", "session-1"]);
    let config = cli.interactive_agent_config(Some("/work".into())).unwrap();
    let params = resume_params("session-1", &config);

    // Defaults are skipped, matching the Python wire shape.
    assert_eq!(params["agentConfig"]["agent"], serde_json::Value::Null);
    assert!(!params["agentConfig"]
        .as_object()
        .unwrap()
        .contains_key("autoApprove"));
    assert!(!params["agentConfig"]
        .as_object()
        .unwrap()
        .contains_key("enabledTools"));
    assert!(!params["agentConfig"]
        .as_object()
        .unwrap()
        .contains_key("disabledTools"));
    assert!(!params["agentConfig"]
        .as_object()
        .unwrap()
        .contains_key("trustWorkspace"));
    assert!(!params["agentConfig"]
        .as_object()
        .unwrap()
        .contains_key("workspaceRoots"));
}
