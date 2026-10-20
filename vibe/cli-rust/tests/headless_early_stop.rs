//! A run stopped before its session opens ends at once, without the stop grace.

use std::io::BufRead;
use std::sync::Arc;
use std::time::{Duration, Instant};

use vibe_rs::cli::{HeadlessOptions, HeadlessSession, OutputFormat};
use vibe_rs::headless::drive;
use vibe_rs::headless_run::StopRequests;
use vibe_rs::run_export::{RunLimits, RunOutcome};
use vibe_rs::server::{Connect, Launch};

#[test]
#[ignore]
fn fake_server_never_answers() {
    for line in std::io::stdin().lock().lines() {
        if line.is_err() {
            break;
        }
    }
}

fn options() -> HeadlessOptions {
    HeadlessOptions {
        prompt: "say hi".to_owned(),
        session: HeadlessSession::New,
        output: OutputFormat::Silent,
        max_turns: None,
        max_price: None,
        max_tokens: None,
        time_limit: None,
        output_dir: None,
        agent: None,
        auto_approve: false,
        enabled_tools: Vec::new(),
        disabled_tools: Vec::new(),
        trust: false,
        add_dir: Vec::new(),
        worktree: None,
        agent_socket: None,
    }
}

#[tokio::test]
async fn a_stop_before_initialize_is_answered_ends_the_run_within_a_second() {
    let launch = Launch {
        program: std::env::current_exe()
            .unwrap()
            .to_string_lossy()
            .into_owned(),
        args: [
            "--ignored",
            "--exact",
            "fake_server_never_answers",
            "--nocapture",
        ]
        .map(str::to_owned)
        .to_vec(),
        cwd: None,
    };
    let stop = Arc::new(StopRequests::new(RunLimits::default()));
    let stopping = stop.clone();
    tokio::spawn(async move {
        tokio::time::sleep(Duration::from_millis(200)).await;
        stopping.request(RunOutcome::Terminated);
    });
    let started = Instant::now();
    let report = drive(&options(), None, Connect::Spawn(launch), &stop).await;

    assert!(
        started.elapsed() < Duration::from_secs(1),
        "{:?}",
        started.elapsed()
    );
    assert_eq!(report.result.outcome, RunOutcome::Terminated);
    assert!(report.result.stop_reason.is_none());
    assert!(report.result.session_id.is_none());
}
