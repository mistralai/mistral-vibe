//! End to end for the `.env` strip: the app-server child must not inherit
//! the keys the client's dotenv load set — the child re-derives the file
//! itself. Spawns this binary as the child through the real `Client::spawn`,
//! so the `env_remove` wiring is exercised, not assumed.

use serde_json::{json, Value};
use std::io::{BufRead, Write};

/// Child half: answer every request with this process's environment.
#[test]
#[ignore]
fn dotenv_env_echo_server() {
    let env: serde_json::Map<String, Value> = std::env::vars()
        .map(|(key, value)| (key, Value::String(value)))
        .collect();
    for line in std::io::stdin().lock().lines() {
        let msg: Value = serde_json::from_str(&line.expect("stdin line")).expect("json line");
        if msg.get("id").is_none() {
            continue;
        }
        let reply = json!({"jsonrpc": "2.0", "id": msg["id"], "result": {"env": env}});
        println!("{}", serde_json::to_string(&reply).expect("reply json"));
        std::io::stdout().flush().expect("flush stdout");
    }
}

#[tokio::test]
async fn client_spawn_strips_the_dotenv_set_keys() {
    let home = tempfile::tempdir().expect("temp home");
    std::fs::write(home.path().join(".env"), "DOTENV_CHILD_FILE=from-file\n").expect(".env");
    std::env::set_var("VIBE_HOME", home.path());
    std::env::set_var("DOTENV_CHILD_SHELL", "from-shell");
    vibe_rs::credentials::dotenv::load_dotenv_values();

    let launch = vibe_rs::server::Launch {
        program: std::env::current_exe()
            .unwrap()
            .to_string_lossy()
            .into_owned(),
        args: vec![
            "--ignored".into(),
            "--exact".into(),
            "dotenv_env_echo_server".into(),
            "--nocapture".into(),
        ],
        cwd: None,
    };
    let (client, child, _notifications, _crash) = vibe_rs::server::Client::spawn(launch)
        .await
        .expect("spawn echo child");

    let env = client
        .request("test/env", json!({}))
        .await
        .expect("env echo")["env"]
        .as_object()
        .expect("env object")
        .clone();
    // The dotenv-set key never reached the child...
    assert!(
        env.get("DOTENV_CHILD_FILE").is_none(),
        "the child must re-derive .env itself: {:?}",
        env.get("DOTENV_CHILD_FILE")
    );
    // ...while a shell-set key still does.
    assert_eq!(
        env.get("DOTENV_CHILD_SHELL").and_then(Value::as_str),
        Some("from-shell")
    );

    drop(child);
    std::env::remove_var("VIBE_HOME");
    std::env::remove_var("DOTENV_CHILD_FILE");
    std::env::remove_var("DOTENV_CHILD_SHELL");
    drop(home);
}
