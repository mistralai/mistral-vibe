//! `vibe run-server`'s backstops: a run past its hard stop, a close never acknowledged, a dead app-server.

mod run_server_common;

use std::time::{Duration, Instant};

use run_server_common::{fake_app_server, RunServer};
use serde_json::json;

#[test]
#[ignore]
fn fake() {
    fake_app_server();
}

/// An app-server that dies as it starts, before it answers anything.
#[test]
#[ignore]
fn dying() {
    std::process::exit(3);
}

#[test]
fn a_run_whose_app_server_dies_once_placed_fails_promptly_and_frees_its_slot() {
    for limits in [json!({}), json!({"timeLimit": 60})] {
        let mut server = RunServer::start("dying", &["--max-runs", "1"]);
        server.initialize();
        let started = Instant::now();
        server.start_run("a", limits.clone());

        let ended = server.ended("a", Duration::from_secs(10));

        assert_eq!(ended["outcome"], "infrastructure_failure", "{limits}");
        assert!(started.elapsed() < Duration::from_secs(10), "{limits}");
        let next = server.start_run("b", json!({}));
        assert_eq!(
            next["result"],
            json!({}),
            "the run's slot is free: {limits}"
        );
    }
}

#[test]
fn a_run_past_its_hard_stop_still_ends_and_frees_its_slot() {
    let mut server = RunServer::start_with_env(
        "fake",
        &["--max-runs", "2", "--runs-per-app-server", "2"],
        &[("VIBE_TEST_HARD_STOP_MS", "500")],
    );
    server.initialize();
    server.start_run("wedge", json!({}));
    server.start_run("b", json!({}));
    std::thread::sleep(Duration::from_millis(300));

    let stopped = Instant::now();
    server.request("run/stop", json!({"runId": "wedge"}));
    let ended = server.ended("wedge", Duration::from_secs(5));

    assert!(
        stopped.elapsed() < Duration::from_secs(5),
        "ended by the hard stop, not the run's own grace"
    );
    assert_eq!(ended["outcome"], "terminated");
    assert_eq!(ended["error"]["code"], "stop_timed_out");
    assert_eq!(
        server.start_run("next", json!({}))["result"],
        json!({}),
        "the run's slot is free"
    );
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(server.app_servers(), 1, "and so is its app-server's");
}

#[cfg(unix)]
#[test]
fn a_close_never_acknowledged_frees_its_slot_and_retires_the_app_server() {
    let mut server = RunServer::start_with_env(
        "fake",
        &["--runs-per-app-server", "2"],
        &[("VIBE_TEST_CLOSE_ACK_TIMEOUT_MS", "500")],
    );
    server.initialize();
    server.start_run("mute", json!({}));
    server.start_run("b", json!({}));
    std::thread::sleep(Duration::from_millis(300));
    let first = server.app_server_pids();

    server.request("run/stop", json!({"runId": "mute"}));
    assert_eq!(
        server.ended("mute", Duration::from_secs(5))["outcome"],
        "terminated"
    );
    std::thread::sleep(Duration::from_millis(1000));
    server.start_run("c", json!({}));
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(
        server.app_servers(),
        2,
        "an app-server that left a close unacknowledged takes no new run"
    );

    server.request("run/stop", json!({"runId": "b"}));
    server.ended("b", Duration::from_secs(5));
    let alive = |pid: i32| unsafe { libc::kill(pid, 0) } == 0;
    let deadline = Instant::now() + Duration::from_secs(10);
    while alive(first[0]) {
        assert!(
            Instant::now() < deadline,
            "the unacknowledged channel still holds its app-server"
        );
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(server
        .logs()
        .contains("did not acknowledge closing a run's channel"));
}

#[cfg(unix)]
#[test]
fn ending_does_not_wait_for_a_close_never_acknowledged() {
    for sigterm in [false, true] {
        let mut server = RunServer::start("fake", &[]);
        server.initialize();
        server.start_run("mute", json!({}));
        std::thread::sleep(Duration::from_millis(300));

        let ending = Instant::now();
        if sigterm {
            unsafe { libc::kill(server.pid() as i32, libc::SIGTERM) };
        } else {
            server.close_stdin();
        }
        let (code, stderr) = server.wait();

        assert_eq!(code, Some(0), "{stderr}");
        assert!(
            ending.elapsed() < Duration::from_secs(10),
            "ended within the app-server's shutdown grace, sigterm={sigterm}"
        );
        assert_eq!(
            server.ended("mute", Duration::from_secs(1))["outcome"],
            "terminated"
        );
        for pid in server.app_server_pids() {
            let alive = unsafe { libc::kill(pid, 0) } == 0;
            assert!(!alive, "app-server {pid} outlived the run server");
        }
    }
}
