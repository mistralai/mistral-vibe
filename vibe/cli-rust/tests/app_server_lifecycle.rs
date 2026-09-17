//! App-server exit classification.

use std::io::{BufRead, Write};
use std::time::Duration;

use vibe_rs::server::{method, Client, Launch};

fn helper_launch(test: &str) -> Launch {
    Launch {
        program: std::env::current_exe()
            .expect("test executable")
            .to_string_lossy()
            .into_owned(),
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            test.into(),
            "--nocapture".into(),
        ],
        cwd: None,
    }
}

#[test]
#[ignore]
fn helper_exits_immediately() {}

#[test]
#[ignore]
fn helper_exits_with_code() {
    std::process::exit(3);
}

#[test]
#[ignore]
fn helper_hangs_forever() {
    loop {
        std::thread::sleep(Duration::from_secs(3600));
    }
}

#[test]
#[ignore]
fn helper_exits_after_one_frame() {
    let mut frame = String::new();
    std::io::stdin()
        .lock()
        .read_line(&mut frame)
        .expect("read frame");
    let request: serde_json::Value = serde_json::from_str(&frame).expect("request frame");
    writeln!(
        std::io::stdout(),
        "{}",
        serde_json::json!({"jsonrpc": "2.0", "id": request["id"], "result": {}})
    )
    .expect("response frame");
}

#[test]
#[ignore]
fn helper_exits_on_stdin_eof() {
    let mut line = String::new();
    while std::io::stdin().lock().read_line(&mut line).unwrap_or(0) > 0 {
        line.clear();
    }
}

#[test]
#[ignore]
fn helper_answers_then_waits_for_eof() {
    let mut frame = String::new();
    std::io::stdin()
        .lock()
        .read_line(&mut frame)
        .expect("read frame");
    let request: serde_json::Value = serde_json::from_str(&frame).expect("request frame");
    let mut out = std::io::stdout();
    writeln!(
        out,
        "{}",
        serde_json::json!({"jsonrpc": "2.0", "id": request["id"], "result": {}})
    )
    .expect("response frame");
    out.flush().expect("flush response");
    let mut rest = String::new();
    while std::io::stdin().lock().read_line(&mut rest).unwrap_or(0) > 0 {
        rest.clear();
    }
}

#[test]
#[ignore]
fn helper_floods_notifications() {
    let mut out = std::io::stdout();
    for index in 0..5000 {
        writeln!(
            out,
            "{}",
            serde_json::json!({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"index": index}
            })
        )
        .expect("notification frame");
    }
    out.flush().expect("flush notifications");
    loop {
        std::thread::sleep(Duration::from_secs(3600));
    }
}

#[tokio::test(flavor = "current_thread")]
async fn notification_burst_backpressures_instead_of_crashing() {
    let (_client, child, mut notifications, crash_rx) =
        Client::spawn(helper_launch("helper_floods_notifications"))
            .await
            .expect("spawn helper");

    // Give the reader time to fill the channel and the pipe.
    tokio::time::sleep(Duration::from_millis(300)).await;
    assert!(
        !*crash_rx.borrow(),
        "reader treated a full notification channel as a crash"
    );

    let mut count = 0u32;
    while count < 5000 {
        match tokio::time::timeout(Duration::from_secs(5), notifications.recv()).await {
            Ok(Some(_)) => count += 1,
            Ok(None) => panic!("notification channel closed after {count} frames"),
            Err(_) => panic!("timed out after {count} frames"),
        }
    }
    assert!(!*crash_rx.borrow());
    drop(child);
}

#[tokio::test]
async fn unexpected_exit_is_reported_as_a_crash() {
    let (_client, child, _notifications, mut crash_rx) =
        Client::spawn(helper_launch("helper_exits_immediately"))
            .await
            .expect("spawn helper");

    tokio::time::timeout(Duration::from_secs(2), crash_rx.changed())
        .await
        .expect("crash signal timeout")
        .expect("crash sender");
    assert!(*crash_rx.borrow());
    let status = child.wait_with_grace().await.expect("self exit status");
    assert!(status.success());
    assert_eq!(status.code(), Some(0));
}

#[tokio::test]
async fn session_stop_exit_is_not_reported_as_a_crash() {
    let (client, child, _notifications, mut crash_rx) =
        Client::spawn(helper_launch("helper_exits_after_one_frame"))
            .await
            .expect("spawn helper");
    client
        .request(
            method::SESSION_STOP,
            serde_json::json!({"sessionId": "test"}),
        )
        .await
        .expect("stop session");

    tokio::time::timeout(Duration::from_secs(2), crash_rx.changed())
        .await
        .expect("shutdown signal timeout")
        .expect("shutdown sender");
    assert!(!*crash_rx.borrow());
    let _ = child.wait_with_grace().await;
}

#[tokio::test]
async fn grace_wait_surfaces_a_nonzero_self_exit() {
    let (_client, child, _notifications, _crash_rx) =
        Client::spawn(helper_launch("helper_exits_with_code"))
            .await
            .expect("spawn helper");
    let status = child.wait_with_grace().await.expect("self exit status");
    assert!(!status.success());
    assert_eq!(status.code(), Some(3));
}

#[tokio::test]
async fn grace_wait_reports_none_when_the_grace_expires() {
    let (_client, child, _notifications, _crash_rx) =
        Client::spawn(helper_launch("helper_hangs_forever"))
            .await
            .expect("spawn helper");
    assert!(child.wait_with_grace().await.is_none());
}

#[tokio::test]
async fn closing_stdin_lets_the_child_exit_before_grace() {
    let (client, child, _notifications, _crash_rx) =
        Client::spawn(helper_launch("helper_exits_on_stdin_eof"))
            .await
            .expect("spawn helper");
    client.close_stdin().await;
    let status = child.wait_with_grace().await.expect("self exit status");
    assert!(status.success());
}

#[tokio::test]
async fn session_stop_then_stdin_close_lets_a_waiting_child_exit() {
    let (client, child, _notifications, mut crash_rx) =
        Client::spawn(helper_launch("helper_answers_then_waits_for_eof"))
            .await
            .expect("spawn helper");
    client
        .request(
            method::SESSION_STOP,
            serde_json::json!({"sessionId": "test"}),
        )
        .await
        .expect("stop session");
    client.close_stdin().await;

    tokio::time::timeout(Duration::from_secs(2), crash_rx.changed())
        .await
        .expect("shutdown signal timeout")
        .expect("shutdown sender");
    assert!(!*crash_rx.borrow());
    assert!(child.wait_with_grace().await.is_some());
}

#[test]
#[ignore]
fn helper_hangs_without_reading_stdin() {
    loop {
        std::thread::sleep(Duration::from_secs(3600));
    }
}

#[tokio::test(flavor = "current_thread")]
async fn close_stdin_is_bounded_when_the_child_never_reads() {
    let (client, _child, _notifications, _crash_rx) =
        Client::spawn(helper_launch("helper_hangs_without_reading_stdin"))
            .await
            .expect("spawn helper");
    // Fill the pipe and the writer queue: the helper never reads stdin, so the
    // writer parks in write_all and every further send queues until full.
    for _ in 0..1024 {
        let blob = "x".repeat(512);
        let frame = serde_json::json!({"blob": blob});
        if tokio::time::timeout(
            Duration::from_millis(100),
            client.notify("initialized", frame),
        )
        .await
        .is_err()
        {
            break;
        }
    }
    // The queue is full: without the grace bound this await hangs forever.
    tokio::time::timeout(Duration::from_secs(4), client.close_stdin())
        .await
        .expect("close_stdin must not hang on a full writer queue");
}

#[test]
#[ignore]
fn helper_floods_then_answers() {
    let mut out = std::io::stdout();
    for index in 0..5000 {
        writeln!(
            out,
            "{}",
            serde_json::json!({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"index": index}
            })
        )
        .expect("notification frame");
    }
    out.flush().expect("flush notifications");
    let mut frame = String::new();
    std::io::stdin()
        .lock()
        .read_line(&mut frame)
        .expect("read frame");
    let request: serde_json::Value = serde_json::from_str(&frame).expect("request frame");
    writeln!(
        out,
        "{}",
        serde_json::json!({"jsonrpc": "2.0", "id": request["id"], "result": {}})
    )
    .expect("response frame");
    out.flush().expect("flush response");
    loop {
        std::thread::sleep(Duration::from_secs(3600));
    }
}

#[tokio::test(flavor = "current_thread")]
async fn responses_route_while_notifications_backpressure() {
    let (client, _child, _notifications, _crash_rx) =
        Client::spawn(helper_launch("helper_floods_then_answers"))
            .await
            .expect("spawn helper");
    // Let the flood fill the 512-cap notification channel; the receiver is
    // never polled, so only the reader's backlog can keep it moving.
    tokio::time::sleep(Duration::from_millis(500)).await;
    let answer = tokio::time::timeout(
        Duration::from_secs(5),
        client.request(
            method::SESSION_STOP,
            serde_json::json!({"sessionId": "test"}),
        ),
    )
    .await
    .expect("request must complete while notifications backpressure")
    .expect("stop session");
    assert_eq!(answer, serde_json::json!({}));
}

#[test]
#[ignore]
fn helper_floods_past_the_backlog_cap() {
    let mut out = std::io::stdout();
    for index in 0..512 {
        writeln!(
            out,
            "{}",
            serde_json::json!({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"index": index}
            })
        )
        .expect("notification frame");
    }
    out.flush().expect("flush notifications");
    let blob = "x".repeat(64 * 1024);
    for _ in 0..400 {
        writeln!(
            out,
            "{}",
            serde_json::json!({
                "jsonrpc": "2.0",
                "method": "session/update",
                "params": {"blob": blob}
            })
        )
        .expect("notification frame");
    }
    out.flush().expect("flush notifications");
    loop {
        std::thread::sleep(Duration::from_secs(3600));
    }
}

#[tokio::test(flavor = "current_thread")]
async fn notification_backlog_overflow_fails_the_transport() {
    let (_client, _child, _notifications, mut crash_rx) =
        Client::spawn(helper_launch("helper_floods_past_the_backlog_cap"))
            .await
            .expect("spawn helper");
    tokio::time::timeout(Duration::from_secs(10), crash_rx.changed())
        .await
        .expect("the backlog cap must stop the reader")
        .expect("crash sender");
    assert!(*crash_rx.borrow(), "overflow must fail the transport");
}
