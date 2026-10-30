//! A client killed outright, which runs no `Drop`, still takes its app-server
//! with it: on Linux the kernel's parent-death signal, a SIGTERM so the
//! app-server can stop its tools first, ends it at once, even one that never
//! reads its stdin to see the client gone. Both spawns are covered: `vibe -p`
//! and the TUI's, and the run server's multiplexed one.
#![cfg(target_os = "linux")]

use std::any::Any;
use std::fs::File;
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use vibe_rs::server::{Client, Launch, MuxClient};

const SPAWN_ENV: &str = "VIBE_TEST_PARENT_DEATH_SPAWN";
const PID_FD: &str = "VIBE_TEST_PARENT_DEATH_PID_FD";
const SIGNALS_ENV: &str = "VIBE_TEST_PARENT_DEATH_SIGNALS";
// Far beyond the kernel's signal; an app-server left alive never ends.
const DEATH_BUDGET: Duration = Duration::from_secs(10);

fn pipe() -> (File, OwnedFd) {
    let mut fds = [0; 2];
    assert_eq!(unsafe { libc::pipe(fds.as_mut_ptr()) }, 0, "pipe failed");
    unsafe { libc::fcntl(fds[0], libc::F_SETFD, libc::FD_CLOEXEC) };
    unsafe { (File::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) }
}

/// Reads `pipe` to its end, which comes once every holder of its write end
/// has exited; false if that takes longer than `budget`.
fn ends_within(mut pipe: File, budget: Duration) -> bool {
    let (tx, rx) = mpsc::channel();
    std::thread::spawn(move || {
        let _ = pipe.read_to_end(&mut Vec::new());
        let _ = tx.send(());
    });
    rx.recv_timeout(budget).is_ok()
}

/// A stand-in app-server that ignores its arguments and never reads its
/// stdin, so the end of the client's pipe cannot end it. It ends on SIGTERM,
/// which it records in `signals` after `ready`, once its foreground `sleep 1`
/// ends: it signals no child, as a signal sent before a child's exec is lost.
fn deaf_app_server(signals: &str) -> Launch {
    let script =
        r#"trap 'echo TERM >> "$0"; exit 0' TERM; echo ready > "$0"; while :; do sleep 1; done"#;
    Launch {
        program: "sh".into(),
        args: vec!["-c".into(), script.into(), signals.into()],
        cwd: None,
    }
}

/// Plays the client: spawns the stand-in as `SPAWN_ENV` says, reports its pid,
/// and waits to be killed.
#[test]
#[ignore]
fn helper_client_spawns_app_server_and_hangs() {
    let fd: i32 = std::env::var(PID_FD).expect(PID_FD).parse().expect(PID_FD);
    let signals = std::env::var(SIGNALS_ENV).expect(SIGNALS_ENV);
    unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) };
    let mut report = unsafe { File::from_raw_fd(fd) };
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("runtime");
    runtime.block_on(async move {
        let (pid, _keep) = match std::env::var(SPAWN_ENV).expect(SPAWN_ENV).as_str() {
            "client" => {
                let (client, child, _notifications, _crash) =
                    Client::spawn(deaf_app_server(&signals))
                        .await
                        .expect("spawn");
                (child.pid(), Box::new((client, child)) as Box<dyn Any>)
            }
            "mux" => {
                let mux = MuxClient::spawn(&deaf_app_server(&signals)).expect("spawn");
                (mux.pid(), Box::new(mux) as Box<dyn Any>)
            }
            other => panic!("unknown spawn {other}"),
        };
        writeln!(report, "{}", pid.expect("app-server pid")).expect("report pid");
        drop(report);
        std::future::pending::<()>().await;
    });
}

/// SIGKILLs a client that spawned the stand-in by `spawn`: whether the
/// stand-in died within `DEATH_BUDGET`, and the signals it recorded.
fn app_server_dies_with_its_client(spawn: &str) -> (bool, Vec<String>) {
    let dir = tempfile::tempdir().expect("tempdir");
    let signals = dir.path().join("signals");
    let (mut pid, pid_w) = pipe();
    // Inherited by the client and from it by the stand-in, never read by
    // either: it reaches its end when both have exited.
    let (alive, alive_w) = pipe();
    let mut client = Command::new(std::env::current_exe().expect("test executable"))
        .args([
            "--ignored",
            "--exact",
            "helper_client_spawns_app_server_and_hangs",
            "--nocapture",
        ])
        .env(SPAWN_ENV, spawn)
        .env(PID_FD, pid_w.as_raw_fd().to_string())
        .env(SIGNALS_ENV, &signals)
        .stdin(Stdio::null())
        .spawn()
        .expect("spawn client");
    drop((pid_w, alive_w));
    let mut line = String::new();
    pid.read_to_string(&mut line).expect("read pid");
    let app_server: i32 = line.trim().parse().expect("app-server pid");
    let ready = Instant::now() + DEATH_BUDGET;
    while std::fs::read_to_string(&signals)
        .unwrap_or_default()
        .is_empty()
    {
        assert!(Instant::now() < ready, "the stand-in never got ready");
        std::thread::sleep(Duration::from_millis(10));
    }

    client.kill().expect("kill client");
    client.wait().expect("reap client");
    let died = ends_within(alive, DEATH_BUDGET);
    // Its own process group; a no-op once it has died.
    unsafe { libc::kill(-app_server, libc::SIGKILL) };
    let recorded = std::fs::read_to_string(&signals).unwrap_or_default();
    let mut recorded: Vec<String> = recorded.split_whitespace().map(String::from).collect();
    // The kernel resends the death signal as each dying client thread hands the stand-in on.
    recorded.dedup();
    (died, recorded)
}

// One sequential test: a concurrent spawn would inherit the pipe write ends
// and delay their end.
#[test]
fn a_killed_client_sigterms_its_app_server() {
    let (died, signals) = app_server_dies_with_its_client("client");
    assert!(died, "the app-server outlived its killed client");
    assert_eq!(
        signals,
        ["ready", "TERM"],
        "the app-server was not sent SIGTERM"
    );
    let (died, signals) = app_server_dies_with_its_client("mux");
    assert!(
        died,
        "the multiplexed app-server outlived its killed run server"
    );
    assert_eq!(
        signals,
        ["ready", "TERM"],
        "the multiplexed app-server was not sent SIGTERM"
    );
}
