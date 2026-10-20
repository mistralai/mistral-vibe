//! `vibe run-server`'s requests, refusals and protocol errors.

mod run_server_common;

use std::time::Duration;

use run_server_common::{fake_app_server, RunServer};
use serde_json::json;

#[test]
#[ignore]
fn fake() {
    fake_app_server();
}

#[test]
fn initialize_comes_first_and_once() {
    let mut server = RunServer::start("fake", &["--max-runs", "3"]);

    assert_eq!(server.request("ping", json!({}))["result"], json!({}));
    let early = server.start_run("a", json!({}));
    assert_eq!(early["error"]["code"], "not_initialized");
    let old = server.request(
        "initialize",
        json!({"protocolVersion": 2, "clientInfo": {"name": "t", "version": "0"}}),
    );
    assert_eq!(old["error"]["code"], "invalid_request");
    assert_eq!(old["error"]["data"], json!({"protocolVersion": 1}));

    let reply = server.request(
        "initialize",
        json!({"protocolVersion": 1, "clientInfo": {"name": "t", "version": "0"}}),
    );
    let result = &reply["result"];
    assert_eq!(result["protocolVersion"], 1);
    assert_eq!(result["serverInfo"]["name"], "vibe-run-server");
    assert_eq!(result["maxRuns"], 3);
    let again = server.request(
        "initialize",
        json!({"protocolVersion": 1, "clientInfo": {"name": "t", "version": "0"}}),
    );
    assert_eq!(again["error"]["code"], "invalid_request");
    assert_eq!(
        server.request("nope", json!({}))["error"]["code"],
        "method_not_found"
    );
}

#[test]
fn invalid_params_name_fields_without_their_values() {
    let mut server = RunServer::start("fake", &[]);
    server.initialize();

    let reply = server.request(
        "run/start",
        json!({"runId": "a", "agentSocket": "s", "maxTurns": "sk-not-a-number", "bogus": 1}),
    );

    let error = &reply["error"];
    assert_eq!(error["code"], "invalid_params");
    let paths: Vec<_> = error["data"]["issues"]
        .as_array()
        .unwrap()
        .iter()
        .map(|issue| issue["path"][0].clone())
        .collect();
    assert!(paths.contains(&json!("maxTurns")), "{error}");
    assert!(paths.contains(&json!("bogus")), "{error}");
    assert!(!reply.to_string().contains("sk-not-a-number"), "{reply}");

    let missing = server.request("run/start", json!({"runId": "a", "timeLimit": -1}));
    let paths = missing["error"]["data"]["issues"].to_string();
    for field in ["prompt", "agentSocket", "timeLimit"] {
        assert!(paths.contains(field), "{field}: {paths}");
    }
}

#[test]
fn live_run_ids_are_unique_and_max_runs_caps_them() {
    let mut server = RunServer::start("fake", &["--max-runs", "1"]);
    server.initialize();

    assert_eq!(server.start_run("a", json!({}))["result"], json!({}));
    let duplicate = server.start_run("a", json!({}));
    assert_eq!(duplicate["error"]["code"], "conflict");
    assert_eq!(duplicate["error"]["data"]["reason"], "duplicate_run");
    let full = server.start_run("b", json!({}));
    assert_eq!(
        full["error"]["data"],
        json!({"reason": "at_capacity", "maxRuns": 1})
    );
    let unknown = server.request("run/stop", json!({"runId": "b"}));
    assert_eq!(unknown["error"]["code"], "not_found");

    assert_eq!(
        server.request("run/stop", json!({"runId": "a"}))["result"],
        json!({})
    );
    let ended = server.ended("a", Duration::from_secs(5));
    assert_eq!(ended["outcome"], "terminated");
    assert_eq!(ended["exitCode"], 3);

    assert_eq!(
        server.start_run("a", json!({}))["result"],
        json!({}),
        "the slot and id are free"
    );
}

#[test]
fn a_malformed_message_ends_every_run_and_exits_one_without_echoing_it() {
    let mut server = RunServer::start("fake", &[]);
    server.initialize();
    server.start_run("a", json!({}));

    server.send_raw(r#"{"jsonrpc": "2.0", "method": "sk-echo-me"}"#);
    let (code, stderr) = server.wait();

    assert_eq!(code, Some(1), "{stderr}");
    let ended = server.ended("a", Duration::from_secs(1));
    assert_eq!(ended["outcome"], "terminated");
    let out = server.stdout.join("\n");
    assert!(!out.contains("sk-echo-me") && !stderr.contains("sk-echo-me"));
}

#[test]
fn stdin_closing_ends_every_run_and_exits_zero() {
    let mut server = RunServer::start("fake", &["--runs-per-app-server", "2"]);
    server.initialize();
    for run in ["a", "b", "c"] {
        server.start_run(run, json!({}));
    }

    let (code, stderr) = server.wait();

    assert_eq!(code, Some(0), "{stderr}");
    for run in ["a", "b", "c"] {
        assert_eq!(
            server.ended(run, Duration::from_secs(1))["outcome"],
            "terminated"
        );
    }
    for line in &server.stdout {
        let message: serde_json::Value = serde_json::from_str(line).unwrap();
        assert_eq!(
            message["jsonrpc"], "2.0",
            "only the protocol on stdout: {line}"
        );
    }
    #[cfg(unix)]
    for pid in server.app_server_pids() {
        let alive = unsafe { libc::kill(pid, 0) } == 0;
        assert!(!alive, "app-server {pid} outlived the run server");
    }
}

#[cfg(unix)]
#[test]
fn sigterm_ends_every_run_and_exits_zero() {
    let mut server = RunServer::start("fake", &[]);
    server.initialize();
    server.start_run("a", json!({}));

    unsafe { libc::kill(server.pid() as i32, libc::SIGTERM) };
    let ended = server.ended("a", Duration::from_secs(5));
    let (code, stderr) = server.wait();

    assert_eq!(ended["outcome"], "terminated");
    assert_eq!(code, Some(0), "{stderr}");
}
