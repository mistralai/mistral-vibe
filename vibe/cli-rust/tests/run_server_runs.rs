//! `vibe run-server`'s runs: pooled app-servers, isolation, limits and redaction.

mod run_server_common;

use std::time::{Duration, Instant};

use run_server_common::{fake_app_server, RunServer, SECRET};
use serde_json::json;

#[test]
#[ignore]
fn fake() {
    fake_app_server();
}

#[test]
fn an_app_server_takes_k_runs_and_the_next_one_starts_another() {
    let mut server = RunServer::start("fake", &["--runs-per-app-server", "2"]);
    server.initialize();

    server.start_run("a", json!({}));
    server.start_run("b", json!({}));
    assert_eq!(server.request("ping", json!({}))["result"], json!({}));
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(server.app_servers(), 1);
    server.start_run("c", json!({}));
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(server.app_servers(), 2);

    server.request("run/stop", json!({"runId": "a"}));
    server.ended("a", Duration::from_secs(5));
    server.start_run("d", json!({}));
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(server.app_servers(), 2, "a freed slot is reused");
}

#[test]
fn an_app_server_takes_16_runs_by_default() {
    let mut server = RunServer::start("fake", &[]);
    server.initialize();
    for run in 0..16 {
        server.start_run(&format!("r{run}"), json!({}));
    }
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(server.app_servers(), 1);
    server.start_run("r16", json!({}));
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(server.app_servers(), 2);
}

#[test]
fn a_run_awaiting_its_close_acknowledgement_takes_no_room() {
    let mut server = RunServer::start("fake", &["--runs-per-app-server", "2"]);
    server.initialize();
    server.start_run("slow-a", json!({}));
    server.start_run("b", json!({}));

    server.request("run/stop", json!({"runId": "slow-a"}));
    server.ended("slow-a", Duration::from_secs(5));
    server.start_run("c", json!({}));
    std::thread::sleep(Duration::from_millis(300));

    assert_eq!(
        server.app_servers(),
        1,
        "a closing channel's slot is taken by the next run, not a new app-server"
    );
}

#[cfg(unix)]
#[test]
fn an_app_server_exits_once_its_last_run_ends() {
    let mut server = RunServer::start("fake", &["--runs-per-app-server", "1"]);
    server.initialize();
    server.start_run("a", json!({}));
    server.start_run("b", json!({}));
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(server.app_servers(), 2);

    server.request("run/stop", json!({"runId": "a"}));
    server.ended("a", Duration::from_secs(5));

    let alive = || -> Vec<i32> {
        let pids = server.app_server_pids();
        pids.into_iter()
            .filter(|&pid| unsafe { libc::kill(pid, 0) } == 0)
            .collect()
    };
    let deadline = Instant::now() + Duration::from_secs(10);
    while alive().len() > 1 {
        assert!(Instant::now() < deadline, "the idle app-server stays");
        std::thread::sleep(Duration::from_millis(50));
    }
    assert_eq!(alive().len(), 1, "the busy app-server stays");
    server.start_run("c", json!({}));
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(server.app_servers(), 3, "a new run starts a new app-server");
}

#[cfg(unix)]
#[test]
fn new_runs_fill_the_fuller_app_server_so_the_other_drains_and_exits() {
    let mut server = RunServer::start("fake", &["--runs-per-app-server", "3"]);
    server.initialize();
    for run in ["a", "b", "c"] {
        server.start_run(run, json!({}));
    }
    std::thread::sleep(Duration::from_millis(300));
    let first = server.app_server_pids();
    assert_eq!(first.len(), 1);
    server.start_run("d", json!({}));
    std::thread::sleep(Duration::from_millis(300));
    let second: Vec<i32> = server
        .app_server_pids()
        .into_iter()
        .filter(|pid| !first.contains(pid))
        .collect();
    assert_eq!(second.len(), 1);

    server.request("run/stop", json!({"runId": "a"}));
    server.ended("a", Duration::from_secs(5));
    server.start_run("e", json!({}));
    server.request("run/stop", json!({"runId": "d"}));
    server.ended("d", Duration::from_secs(5));

    let alive = |pid: i32| unsafe { libc::kill(pid, 0) } == 0;
    let deadline = Instant::now() + Duration::from_secs(10);
    while alive(second[0]) {
        assert!(
            Instant::now() < deadline,
            "the emptier app-server took the new run and stays"
        );
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(alive(first[0]), "the fuller app-server stays");
    assert_eq!(server.app_servers(), 2, "the new run started no app-server");
    for run in ["b", "c", "e"] {
        server.request("run/stop", json!({"runId": run}));
        assert_eq!(
            server.ended(run, Duration::from_secs(5))["outcome"],
            "terminated"
        );
    }
}

#[test]
fn a_crashed_app_server_fails_only_its_own_runs() {
    let mut server = RunServer::start("fake", &["--runs-per-app-server", "1"]);
    server.initialize();
    server.start_run("alive", json!({}));

    server.start_run("crash", json!({}));
    let crashed = server.ended("crash", Duration::from_secs(5));

    assert_eq!(crashed["outcome"], "infrastructure_failure");
    assert_eq!(crashed["exitCode"], 2);
    server.start_run("after", json!({}));
    std::thread::sleep(Duration::from_millis(300));
    assert_eq!(
        server.app_servers(),
        3,
        "the crashed app-server takes no new run"
    );
    assert_eq!(
        server.request("run/stop", json!({"runId": "alive"}))["result"],
        json!({})
    );
    assert_eq!(
        server.ended("alive", Duration::from_secs(5))["outcome"],
        "terminated"
    );
}

#[test]
fn app_servers_get_two_malloc_arenas_unless_the_user_chose() {
    for (env, expected) in [(None, "2"), (Some("8"), "8")] {
        let env: Vec<(&str, &str)> = env.map(|v| ("MALLOC_ARENA_MAX", v)).into_iter().collect();
        let mut server = RunServer::start_with_env("fake", &[], &env);
        server.initialize();
        server.start_run("a", json!({}));
        std::thread::sleep(Duration::from_millis(300));

        assert_eq!(server.app_server_arenas(), [expected]);
    }
}

#[test]
fn a_time_limit_counts_from_run_start() {
    let mut server = RunServer::start("fake", &[]);
    server.initialize();
    let started = Instant::now();

    server.start_run("a", json!({"timeLimit": 0.5}));
    let ended = server.ended("a", Duration::from_secs(5));

    assert_eq!(ended["outcome"], "deadline");
    assert_eq!(ended["exitCode"], 3);
    assert!(started.elapsed() < Duration::from_secs(3));
}

#[test]
fn credentials_reach_the_app_server_but_nothing_the_client_or_logs_see() {
    let mut server = RunServer::start("fake", &[]);
    server.initialize();
    let output_dir = server.home.join("out");

    server.start_run(
        "leak",
        json!({"credentials": {"MISTRAL_API_KEY": SECRET}, "outputDir": output_dir}),
    );
    let ended = server.ended("leak", Duration::from_secs(5));
    let (code, stderr) = server.wait();

    assert_eq!(code, Some(0));
    assert_eq!(ended["outcome"], "infrastructure_failure");
    let message = ended["error"]["message"].as_str().unwrap();
    assert!(message.contains("bad key [REDACTED]"), "{message}");
    let export_path = ended["exportPath"].as_str().unwrap();
    let export = std::fs::read_to_string(export_path).unwrap();
    assert!(export.contains("[REDACTED]"), "{export}");
    for (what, text) in [
        ("stdout", server.stdout.join("\n")),
        ("stderr", stderr),
        ("logs", server.logs()),
        ("export", export),
    ] {
        assert!(
            !text.contains(SECRET),
            "{what} leaks the credential: {text}"
        );
    }
}

#[test]
fn each_log_line_of_a_run_names_it() {
    let mut server = RunServer::start("fake", &[]);
    server.initialize();
    server.start_run("tagged", json!({"timeLimit": 0.2}));
    server.ended("tagged", Duration::from_secs(5));
    server.wait();

    let logs = server.logs();
    let ended = logs
        .lines()
        .find(|line| line.contains("Run ended"))
        .unwrap();
    assert!(ended.contains("run{run=tagged}"), "{ended}");
}
