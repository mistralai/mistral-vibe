//! A scripted app-server double for the wizard's `setup/*` surface: every
//! request is recorded (method + params) and answered per the script, so
//! the five-outcome contract and the old-server skew pin end to end.
//! Included per test binary via `#[path]`; the `#[ignore]` entry points
//! live at the including binary's top level so `--exact` names stay stable.

#[path = "status.rs"]
mod status;

use status::default_status;
use std::io::{BufRead, Write};
use std::sync::Arc;

use serde_json::{json, Value};

/// How the scripted server answers the setup methods.
#[derive(Clone, Copy, Default)]
pub(crate) struct SetupScript {
    /// `setup/store-credential`'s outcome ("env_var_error" | "save_error").
    pub store_outcome: Option<&'static str>,
    /// `setup/submit-choices`'s outcome ("provider_config_error").
    pub submit_outcome: Option<&'static str>,
    /// Answer every `setup/*` method with method-not-found (the old server).
    pub absent: bool,
}

/// The server's resolved view, the same defaults a fresh install seeds
/// from; the wire is camelCase (`model_dump(by_alias=True)`, `to_camel`),
/// and the canonical shape is pinned by `tests/units/setup_wire.rs`'s
/// fixture. A named provider resolves through the runtime's own
/// `_named_provider` path — the loop-guard contract's wire half.
pub(crate) fn status_result(params: &Value) -> Value {
    if params.get("provider").and_then(Value::as_str) == Some("llamacpp") {
        return json!({
            "provider": {
                "name": "llamacpp",
                "apiBase": "http://127.0.0.1:8080/v1",
                "apiKeyEnvVar": "",
                "browserAuthBaseUrl": null,
                "browserAuthApiBaseUrl": null,
                "browserAuthAllowOriginRewrite": false,
            },
            "consoleBaseUrl": "https://console.mistral.ai",
            "vibeBaseUrl": "https://chat.mistral.ai",
            "theme": "auto",
            "supportsBrowserSignIn": false,
            "hasApiKey": false,
        });
    }
    // The shared typed fixture serialized with the wire's own camelCase
    // (the shape pinned byte-for-byte by `tests/units/setup_wire.rs`).
    serde_json::to_value(default_status()).unwrap()
}

/// One scripted conversation loop, the skeleton every fake shares: record
/// each request (method + params), serve the log on `test/requests`, and
/// answer every call with `answer` — its `Err` is the JSON-RPC error
/// object the script fails the call with.
pub(crate) fn serve_scripted(answer: impl Fn(&str, &Value) -> Result<Value, Value>) {
    let mut requests: Vec<Value> = Vec::new();
    for line in std::io::stdin().lock().lines() {
        let msg: Value = serde_json::from_str(&line.unwrap()).unwrap();
        let method = msg["method"].as_str().unwrap_or_default().to_owned();
        if msg.get("id").is_none() {
            continue;
        }
        if method == "test/requests" {
            let reply = json!({
                "jsonrpc": "2.0", "id": msg["id"],
                "result": {"requests": requests},
            });
            println!("{}", serde_json::to_string(&reply).unwrap());
            std::io::stdout().flush().unwrap();
            continue;
        }
        requests.push(
            json!({"method": method, "params": msg.get("params").cloned().unwrap_or(Value::Null)}),
        );
        let reply = match answer(&method, &msg) {
            Ok(result) => json!({"jsonrpc": "2.0", "id": msg["id"], "result": result}),
            Err(error) => json!({"jsonrpc": "2.0", "id": msg["id"], "error": error}),
        };
        println!("{}", serde_json::to_string(&reply).unwrap());
        std::io::stdout().flush().unwrap();
    }
}

/// One scripted conversation: record every request, answer per script,
/// and serve the log on `test/requests`.
pub(crate) fn serve(script: SetupScript) {
    serve_scripted(move |method, msg| {
        let result = match method {
            "initialize" => json!({"serverInfo": {"version": "test"}}),
            "setup/status" if script.absent => Value::Null,
            "setup/status" => status_result(msg.get("params").unwrap_or(&Value::Null)),
            "setup/store-credential" if script.absent => Value::Null,
            "setup/store-credential" => match script.store_outcome {
                Some("env_var_error") => json!({"outcome": "env_var_error", "detail": ""}),
                Some(_) => json!({"outcome": "save_error", "detail": "no space left"}),
                None => json!({"outcome": "completed"}),
            },
            "setup/submit-choices" if script.absent => Value::Null,
            "setup/submit-choices" => match script.submit_outcome {
                Some(_) => json!({"outcome": "provider_config_error", "failures": ["provider"]}),
                None => json!({"outcome": "completed", "failures": []}),
            },
            _ => json!({}),
        };
        if script.absent && method.starts_with("setup/") {
            // The app-server's `ProtocolErrorCode.METHOD_NOT_FOUND`.
            Err(json!({
                "code": "method_not_found",
                "message": format!("Method not found: {method}"),
            }))
        } else {
            Ok(result)
        }
    });
}

/// Spawn a fake server child; the handle must outlive the conversation
/// (`kill_on_drop` tears the process group down when it drops).
pub(crate) async fn spawn(
    server: &str,
) -> (Arc<vibe_rs::server::Client>, vibe_rs::server::ChildHandle) {
    let launch = vibe_rs::server::Launch {
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
    let (client, child, _notifications, _crash) = vibe_rs::server::Client::spawn(launch)
        .await
        .expect("spawn fake server");
    (Arc::new(client), child)
}

/// The requests the fake server recorded so far (method + params).
pub(crate) async fn requests(client: &vibe_rs::server::Client) -> Vec<Value> {
    client
        .request("test/requests", json!({}))
        .await
        .expect("request log")["requests"]
        .as_array()
        .expect("requests")
        .clone()
}

/// The recorded params of every call to `method`.
pub(crate) async fn params_of(client: &vibe_rs::server::Client, method: &str) -> Vec<Value> {
    requests(client)
        .await
        .into_iter()
        .filter(|request| request["method"] == method)
        .map(|request| request["params"].clone())
        .collect()
}
