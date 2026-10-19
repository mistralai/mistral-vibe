//! `vibe -p --agent-socket`: usage checks, the abort, and how a failed session open ends the run.
use std::path::Path;
use std::sync::Arc;

use clap::Parser;
use serde_json::json;
use vibe_rs::agent_socket::{abort_message, app_server_args, sandboxed, server_path, usage_error};
use vibe_rs::cli::{Cli, HeadlessSession};
use vibe_rs::headless::open_failure;
use vibe_rs::headless_run::StopRequests;
use vibe_rs::run_export::{RunLimits, RunOutcome};
use vibe_rs::server::{Notification, RpcError};

fn cli(args: &[&str]) -> Cli {
    Cli::parse_from(["vibe-rs"].iter().chain(args))
}

#[test]
fn no_flag_is_no_usage_error() {
    assert_eq!(
        usage_error(&cli(&["-p", "hi", "--workdir", "."]), false),
        None
    );
}

#[test]
fn a_programmatic_run_on_posix_is_allowed() {
    let run = cli(&[
        "--prompt-file",
        "p.txt",
        "--agent-socket",
        "/s",
        "--resume",
        "id",
    ]);
    assert_eq!(usage_error(&run, false), None);
}

#[test]
fn the_flag_needs_programmatic_mode() {
    assert_eq!(
        usage_error(&cli(&["--agent-socket", "/s"]), false).as_deref(),
        Some("--agent-socket needs programmatic mode: pass -p or --prompt-file")
    );
}

#[test]
fn the_flag_is_refused_on_windows() {
    assert_eq!(
        usage_error(&cli(&["-p", "hi", "--agent-socket", "/s"]), true).as_deref(),
        Some("--agent-socket needs Unix domain sockets; not supported on Windows")
    );
}

#[test]
fn options_that_pick_a_host_directory_or_session_are_named() {
    let run = cli(&[
        "--prompt-file",
        "p.txt",
        "--agent-socket",
        "/s",
        "--workdir",
        ".",
        "--worktree",
        "--add-dir",
        ".",
        "-c",
        "--teleport",
    ]);
    assert_eq!(
        usage_error(&run, false).as_deref(),
        Some(
            "--agent-socket cannot be combined with --workdir, --worktree, --add-dir, \
             --continue, --teleport"
        )
    );
}

#[test]
fn the_socket_and_resume_reach_the_headless_options() {
    let prompt = tempfile::NamedTempFile::new().unwrap();
    std::fs::write(prompt.path(), "hi").unwrap();
    let prompt = prompt.path().to_str().unwrap();
    let mut run = cli(&[
        "--prompt-file",
        prompt,
        "--agent-socket",
        "s.sock",
        "--resume",
        "id",
    ]);
    run.resolve_headless_paths(Path::new("/caller"));
    let options = run.headless_options(None).unwrap();
    assert_eq!(options.agent_socket.as_deref(), Some(Path::new("s.sock")));
    assert_eq!(options.session, HeadlessSession::Resume("id".into()));
}

#[test]
fn a_relative_socket_path_stays_relative_when_the_app_server_shares_the_cwd() {
    let caller = Path::new("/caller");
    assert_eq!(
        server_path(Path::new("s.sock"), Some(caller), Some(caller)),
        Path::new("s.sock")
    );
    assert_eq!(
        server_path(
            Path::new("s.sock"),
            Some(caller),
            Some(Path::new("/project"))
        ),
        Path::new("/caller/s.sock")
    );
    assert_eq!(
        server_path(
            Path::new("/tmp/s.sock"),
            Some(caller),
            Some(Path::new("/project"))
        ),
        Path::new("/tmp/s.sock")
    );
}

#[test]
fn the_app_server_is_handed_the_socket() {
    assert_eq!(
        app_server_args(Path::new("/tmp/a.sock")),
        ["--agent-socket".to_owned(), "/tmp/a.sock".to_owned()]
    );
}

fn rpc_error(code: &str, data: Option<serde_json::Value>) -> RpcError {
    serde_json::from_value(json!({ "code": code, "message": "Out of budget", "data": data }))
        .unwrap()
}

#[test]
fn a_failed_handshake_ends_the_run_as_the_server_names_it() {
    for (outcome, name) in [
        (RunOutcome::Aborted, "aborted"),
        (RunOutcome::UsageError, "usage_error"),
        (RunOutcome::InfrastructureFailure, "infrastructure_failure"),
    ] {
        let data = json!({ "agentSocketOutcome": name });
        assert_eq!(
            open_failure(&rpc_error("internal_error", Some(data))),
            outcome
        );
    }
    assert_eq!(
        open_failure(&rpc_error("internal_error", None)),
        RunOutcome::InfrastructureFailure
    );
}

#[test]
fn an_abort_notification_carries_the_servers_message() {
    let notification = |method: &str| Notification {
        method: method.to_owned(),
        params: json!({ "message": "Out of budget" }),
    };
    assert_eq!(
        abort_message(&[
            notification("history/entryAdded"),
            notification("agentSocket/aborted")
        ]),
        Some("Out of budget".to_owned())
    );
    assert_eq!(abort_message(&[notification("history/entryAdded")]), None);
}

#[test]
fn an_abort_stops_the_run_with_the_servers_message() {
    let stop = Arc::new(StopRequests::new(RunLimits::default()));
    stop.abort("Out of budget");
    stop.abort("Later");
    assert_eq!(stop.requested_outcome(), Some(RunOutcome::Aborted));
    assert_eq!(stop.abort_error().unwrap().message, "Out of budget");
}

#[test]
fn an_abort_after_another_stop_does_not_win() {
    let stop = StopRequests::new(RunLimits::default());
    stop.request(RunOutcome::Terminated);
    stop.abort("Out of budget");
    assert_eq!(stop.requested_outcome(), Some(RunOutcome::Terminated));
    assert!(stop.abort_error().is_none());
}

#[test]
fn an_aborted_run_exits_4() {
    assert_eq!(RunOutcome::Aborted.exit_code(), 4);
    assert_eq!(RunOutcome::Aborted.to_string(), "aborted");
    assert_eq!(
        serde_json::to_value(RunOutcome::Aborted).unwrap(),
        "aborted"
    );
}

#[test]
fn a_session_elsewhere_than_this_directory_is_sandboxed() {
    let here = std::env::temp_dir();
    let here = here.to_str().unwrap();
    assert!(!sandboxed(Some(here), Some(here)));
    assert!(sandboxed(Some("/no/such/sandbox/workspace"), Some(here)));
    assert!(!sandboxed(None, Some(here)));
}

#[test]
fn a_resumed_run_counts_its_usage_from_its_start() {
    use vibe_rs::headless::UsageBaseline;
    let baseline = UsageBaseline::from_stats(&json!({
        "sessionPromptTokens": 6,
        "sessionCompletionTokens": 8,
        "sessionCachedTokens": 0,
        "modelCalls": 0,
    }));
    let stats = json!({
        "sessionPromptTokens": 9,
        "sessionCompletionTokens": 12,
        "sessionCachedTokens": 0,
        "modelCalls": 1,
    });
    assert_eq!(
        baseline.run_stats(&stats),
        json!({
            "sessionPromptTokens": 3,
            "sessionCompletionTokens": 4,
            "sessionCachedTokens": 0,
            "modelCalls": 1,
        })
    );
    assert_eq!(UsageBaseline::default().run_stats(&stats), stats);
}
