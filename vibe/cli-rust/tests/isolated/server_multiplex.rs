//! Runs sharing one app-server through multiplexed channels, against a scripted server.

use std::collections::HashMap;
use std::io::{BufRead, Write};
use std::time::{Duration, Instant};

use serde_json::{json, Value};
use vibe_rs::server::multiplex::CLOSE_ACK_TIMEOUT;
use vibe_rs::server::{notification, Connected, Launch, MuxClient};

fn send(frame: Value) {
    println!("{frame}");
    std::io::stdout().flush().unwrap();
}

/// Answers `test/*` methods per channel; never acknowledges closing a channel opened as `silent`.
#[test]
#[ignore]
fn fake_mux_server() {
    let mut opens: HashMap<String, Value> = HashMap::new();
    let mut open_frames = 0;
    for line in std::io::stdin().lock().lines() {
        let frame: Value = serde_json::from_str(&line.unwrap()).unwrap();
        let channel = frame["channelId"].as_str().unwrap().to_owned();
        if let Some(open) = frame.get("open") {
            open_frames += 1;
            opens.insert(channel.clone(), open.clone());
        }
        let message = &frame["message"];
        if message.is_null() {
            if opens[&channel]["runId"] != "silent" {
                send(json!({"channelId": channel, "message": null}));
            }
            continue;
        }
        let reply = |result: Value| {
            send(
                json!({"channelId": channel, "message": {"jsonrpc": "2.0", "id": message["id"], "result": result}}),
            );
        };
        match message["method"].as_str().unwrap() {
            "test/open" => reply(json!({"open": opens[&channel], "openFrames": open_frames})),
            "test/notify" => {
                let note = json!({"jsonrpc": "2.0", "method": "test/note", "params": {"channel": channel}});
                send(json!({"channelId": channel, "message": note}));
                reply(Value::Null);
            }
            "test/huge" => {
                let padding = "x".repeat(17 * 1024 * 1024);
                send(
                    json!({"channelId": channel, "message": {"jsonrpc": "2.0", "id": message["id"], "result": padding}}),
                );
            }
            "test/exit" => std::process::exit(0),
            "test/hang" => {}
            _ => reply(Value::Null),
        }
    }
}

fn spawn_mux() -> MuxClient {
    let launch = Launch {
        program: std::env::current_exe()
            .unwrap()
            .to_string_lossy()
            .into_owned(),
        // `--` makes the app-server's `--multiplex` one more test name filter.
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            "--nocapture".into(),
            "--".into(),
            test_name!("fake_mux_server"),
        ],
        cwd: None,
    };
    MuxClient::spawn(&launch).unwrap()
}

fn open(mux: &MuxClient, run_id: &str) -> Connected {
    mux.open_channel(
        &json!({"runId": run_id, "agentSocket": "/sock", "credentials": {"KEY": "secret"}}),
    )
}

async fn until(what: &str, done: impl Fn() -> bool) {
    let deadline = Instant::now() + CLOSE_ACK_TIMEOUT + Duration::from_secs(5);
    while !done() {
        assert!(Instant::now() < deadline, "timed out waiting until {what}");
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

#[tokio::test]
async fn each_channel_sends_its_open_options_with_its_first_message_only() {
    let mux = spawn_mux();
    let a = open(&mux, "a");
    let b = open(&mux, "b");

    let first_a = a.client.request("test/open", json!({})).await.unwrap();
    let first_b = b.client.request("test/open", json!({})).await.unwrap();
    let again_a = a.client.request("test/open", json!({})).await.unwrap();

    assert_eq!(first_a["open"]["runId"], "a");
    assert_eq!(first_a["open"]["credentials"], json!({"KEY": "secret"}));
    assert_eq!(first_b["open"]["runId"], "b");
    assert_eq!(again_a["openFrames"], 2);
    assert_eq!(mux.load(), 2);
}

#[tokio::test]
async fn notifications_reach_only_their_channel() {
    let mux = spawn_mux();
    let mut a = open(&mux, "a");
    let mut b = open(&mux, "b");
    b.client.request("test/ping", json!({})).await.unwrap();

    a.client.request("test/notify", json!({})).await.unwrap();

    let note = a.notifications.recv().await.unwrap();
    assert_eq!(note.method, "test/note");
    assert_eq!(note.params["channel"], "1");
    assert!(b.notifications.try_recv().is_err());
}

#[tokio::test]
async fn a_closed_channel_counts_until_the_server_acknowledges_it() {
    let mux = spawn_mux();
    let a = open(&mux, "a");
    let never_sent = open(&mux, "b");
    a.client.request("test/ping", json!({})).await.unwrap();
    assert_eq!(mux.load(), 2);

    drop(never_sent);
    assert_eq!(
        mux.load(),
        1,
        "a channel the server never saw closes at once"
    );
    drop(a);
    assert_eq!(mux.live_runs(), 0, "a closed channel serves no run");
    until("the close is acknowledged", || mux.load() == 0).await;
    assert!(mux.accepts_runs());
}

#[tokio::test]
async fn the_last_channel_closing_retires_the_server() {
    let mux = spawn_mux();
    let a = open(&mux, "a");
    let b = open(&mux, "b");
    a.client.request("test/ping", json!({})).await.unwrap();
    b.client.request("test/ping", json!({})).await.unwrap();

    drop(a);
    until("the close is acknowledged", || mux.load() == 1).await;
    let busy = tokio::time::timeout(Duration::from_millis(300), mux.idle()).await;
    assert!(busy.is_err(), "a server with a channel is not idle");
    assert!(mux.accepts_runs());

    drop(b);
    tokio::time::timeout(Duration::from_secs(5), mux.idle())
        .await
        .expect("the server goes idle");
    assert!(!mux.accepts_runs());
    let refused = mux.try_open_channel(&json!({"runId": "late"}));
    assert!(refused.is_none(), "an idle server takes no new run");
}

#[tokio::test]
async fn an_unacknowledged_close_retires_the_server() {
    let wait = Duration::from_millis(500);
    std::env::set_var(
        "VIBE_TEST_CLOSE_ACK_TIMEOUT_MS",
        wait.as_millis().to_string(),
    );
    let mux = spawn_mux();
    let silent = open(&mux, "silent");
    silent.client.request("test/ping", json!({})).await.unwrap();
    let closed = Instant::now();

    drop(silent);
    until("the close wait ends", || mux.load() == 0).await;

    assert!(closed.elapsed() >= wait);
    assert!(!mux.accepts_runs());
}

#[tokio::test]
async fn the_server_ending_fails_every_channel() {
    let mux = spawn_mux();
    let a = open(&mux, "a");
    let b = open(&mux, "b");
    b.client.request("test/ping", json!({})).await.unwrap();
    let pending = b.client.send_now("test/hang", json!({})).unwrap();

    let exit = a.client.send_now("test/exit", json!({})).unwrap();

    let failure = pending.await.unwrap().unwrap_err();
    assert_eq!(failure.to_string(), "app-server closed");
    assert!(exit.await.unwrap().is_err());
    until("the server is gone", || !mux.accepts_runs()).await;
    assert_eq!(mux.load(), 0);
    let late = open(&mux, "late");
    assert!(late.client.request("test/ping", json!({})).await.is_err());
}

/// The channel's reader has ended, failing what it was waiting on.
async fn until_disconnected(channel: &mut Connected) {
    let note = channel.notifications.recv().await.unwrap();
    assert_eq!(note.method, notification::SERVER_DISCONNECTED);
}

async fn first_request_fails(channel: &Connected) {
    let first = channel.client.request("test/ping", json!({}));
    let first = tokio::time::timeout(Duration::from_secs(5), first).await;
    let failure = first
        .expect("the request is failed, not dropped")
        .unwrap_err();
    assert!(
        format!("{failure:#}").contains("app-server closed"),
        "{failure:#}"
    );
}

#[tokio::test]
async fn a_channel_whose_server_ends_before_its_first_request_fails_it() {
    let mux = spawn_mux();
    let mut placed = open(&mux, "placed");
    let a = open(&mux, "a");

    let _exit = a.client.send_now("test/exit", json!({})).unwrap();
    until_disconnected(&mut placed).await;

    first_request_fails(&placed).await;
    assert_eq!(mux.load(), 0);
}

#[tokio::test]
async fn a_channel_opened_on_an_ended_server_fails_its_first_request() {
    let mux = spawn_mux();
    let a = open(&mux, "a");
    let _exit = a.client.send_now("test/exit", json!({})).unwrap();
    until("the server is gone", || !mux.accepts_runs()).await;

    let mut late = open(&mux, "late");
    until_disconnected(&mut late).await;

    first_request_fails(&late).await;
}

#[tokio::test]
async fn an_oversized_frame_fails_only_its_channel() {
    let mux = spawn_mux();
    let a = open(&mux, "a");
    let b = open(&mux, "b");
    b.client.request("test/ping", json!({})).await.unwrap();

    let huge = a.client.request("test/huge", json!({})).await;

    assert!(huge.is_err());
    b.client.request("test/ping", json!({})).await.unwrap();
    assert!(mux.accepts_runs());
}

#[tokio::test]
async fn shutdown_ends_the_server() {
    let mux = spawn_mux();
    let a = open(&mux, "a");
    a.client.request("test/ping", json!({})).await.unwrap();
    let started = Instant::now();

    mux.shutdown().await;

    assert!(started.elapsed() < Duration::from_secs(2));
    assert!(mux.pid().is_none());
    until("its channels end", || mux.load() == 0).await;
}
