//! `/voice` draft persistence against a scripted app-server.

use std::io::{BufRead, Write};
use std::sync::Arc;
use std::time::Duration;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::{json, Value};
use tokio::sync::mpsc;
use vibe_rs::app::App;
use vibe_rs::commands::CommandEvent;
use vibe_rs::server::{ChildHandle, Client, Launch};
use vibe_rs::voice_app;

fn serve(reject: bool) {
    let mut counts = json!({"config/write": 0, "telemetry/record": 0});
    for line in std::io::stdin().lock().lines() {
        let msg: Value = serde_json::from_str(&line.unwrap()).unwrap();
        let method = msg["method"].as_str().unwrap_or_default().to_owned();
        if msg.get("id").is_none() {
            continue;
        }
        if let Some(count) = counts.get_mut(&method) {
            *count = json!(count.as_u64().unwrap() + 1);
        }
        let result = match method.as_str() {
            "config/write" if reject => json!({"rejected": true, "failures": []}),
            "config/write" => {
                let ops = msg["params"]["ops"].clone();
                let value = |path: &str| {
                    ops.as_array()
                        .unwrap()
                        .iter()
                        .find(|op| op["path"] == path)
                        .map_or(json!(false), |op| op["value"].clone())
                };
                json!({
                    "ops": ops,
                    "runtime": {"config": {
                        "voiceModeEnabled": value("/voice_mode_enabled"),
                        "narratorEnabled": value("/narrator_enabled"),
                    }},
                })
            }
            "test/counts" => counts.clone(),
            _ => json!({}),
        };
        let reply = json!({"jsonrpc": "2.0", "id": msg["id"], "result": result});
        println!("{}", serde_json::to_string(&reply).unwrap());
        std::io::stdout().flush().unwrap();
    }
}

#[test]
#[ignore]
fn fake_server_accepts_config_writes() {
    serve(false);
}

#[test]
#[ignore]
fn fake_server_rejects_config_writes() {
    serve(true);
}

async fn spawn(server: &str) -> (Arc<Client>, ChildHandle) {
    let launch = Launch {
        program: std::env::current_exe()
            .unwrap()
            .to_string_lossy()
            .into_owned(),
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            server.into(),
            "--nocapture".into(),
        ],
        cwd: None,
    };
    let (client, child, _notifications, _crash) = Client::spawn(launch).await.unwrap();
    (Arc::new(client), child)
}

fn press(app: &mut App, client: &Arc<Client>, code: KeyCode) {
    voice_app::handle_key(app, client, KeyEvent::new(code, KeyModifiers::NONE));
}

fn open_app() -> (App, mpsc::Receiver<CommandEvent>) {
    let mut app = App::default();
    let (tx, rx) = mpsc::channel(4);
    app.command_tx = Some(tx);
    app.session.session_id = Some("session-1".to_owned());
    voice_app::open(&mut app);
    (app, rx)
}

async fn next_event(rx: &mut mpsc::Receiver<CommandEvent>) -> CommandEvent {
    tokio::time::timeout(Duration::from_secs(10), rx.recv())
        .await
        .expect("config/write answer timed out")
        .expect("command channel closed")
}

async fn counts(client: &Client) -> (u64, u64) {
    let counts = client.request("test/counts", json!({})).await.unwrap();
    (
        counts["config/write"].as_u64().unwrap(),
        counts["telemetry/record"].as_u64().unwrap(),
    )
}

#[tokio::test]
async fn escape_writes_only_the_changed_setting_once_and_records_the_toggle() {
    let (client, _child) = spawn("fake_server_accepts_config_writes").await;
    let (mut app, mut rx) = open_app();

    press(&mut app, &client, KeyCode::Char(' '));
    press(&mut app, &client, KeyCode::Char(' '));
    press(&mut app, &client, KeyCode::Char(' '));
    press(&mut app, &client, KeyCode::Esc);

    let CommandEvent::VoiceSettings {
        runtime,
        previous_enabled,
        enabling_audio,
    } = next_event(&mut rx).await
    else {
        panic!("expected VoiceSettings");
    };
    assert_eq!(
        runtime["ops"],
        json!([{"op": "set", "path": "/voice_mode_enabled", "value": true, "targetLayer": null}])
    );
    assert!(!previous_enabled);
    assert!(enabling_audio);
    let deadline = tokio::time::Instant::now() + Duration::from_secs(10);
    while counts(&client).await != (1, 1) {
        assert!(
            tokio::time::Instant::now() < deadline,
            "telemetry not recorded"
        );
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
}

#[tokio::test]
async fn narrator_only_change_skips_the_voice_mode_telemetry() {
    let (client, _child) = spawn("fake_server_accepts_config_writes").await;
    let (mut app, mut rx) = open_app();

    press(&mut app, &client, KeyCode::Down);
    press(&mut app, &client, KeyCode::Enter);
    press(&mut app, &client, KeyCode::Esc);

    let CommandEvent::VoiceSettings { runtime, .. } = next_event(&mut rx).await else {
        panic!("expected VoiceSettings");
    };
    assert_eq!(runtime["ops"][0]["path"], "/narrator_enabled");
    assert_eq!(runtime["ops"].as_array().unwrap().len(), 1);
    assert_eq!(counts(&client).await, (1, 0));
}

#[tokio::test]
async fn rejected_write_reports_an_error_and_skips_telemetry() {
    let (client, _child) = spawn("fake_server_rejects_config_writes").await;
    let (mut app, mut rx) = open_app();

    press(&mut app, &client, KeyCode::Char(' '));
    press(&mut app, &client, KeyCode::Esc);

    let CommandEvent::Error(message) = next_event(&mut rx).await else {
        panic!("expected Error");
    };
    assert_eq!(
        message,
        "Failed to apply: voice settings — Invalid configuration edit"
    );
    assert_eq!(counts(&client).await, (1, 0));
}
