//! The background boot's contract against a scripted app-server: a keyed
//! launch never calls `setup/status` — the probe-negative launch's boot
//! lives in the wizard's pre-loop round now — and goes straight to the
//! session handshake on the same child (one task owning the ready channel
//! end to end, so after the boot task ends nothing else can ever arrive on
//! it).

#[allow(dead_code)]
#[path = "onboarding_common/fake_server.rs"]
mod fake_server;

use std::sync::Arc;

use serde_json::json;
use vibe_rs::cli::StartupResume;
use vibe_rs::server::{AgentConfig, Client};
use vibe_rs::setup::boot::background_boot;
use vibe_rs::startup::{HandshakeParams, StartupEvent};

use fake_server::requests;

#[test]
#[ignore]
fn boot_keyed_server() {
    serve_boot();
}

/// The boot's scripted conversation: the handshake's methods answered
/// through the shared stdin loop the setup fake serves its own scripts with.
fn serve_boot() {
    fake_server::serve_scripted(|method, _msg| match method {
        "initialize" => Ok(json!({"serverInfo": {"version": "test"}})),
        "session/start" => Ok(json!({
            "state": {"eventId": 1, "session": {"id": "session-7"}},
        })),
        "runtime/read" => Ok(json!({
            "ready": true,
            "runtime": {
                "config": {
                    "activeModel": {"displayName": "Test", "thinking": "off"},
                    "theme": "dark",
                },
            },
        })),
        "config/read" => Ok(json!({"config": {}})),
        _ => Ok(json!({})),
    });
}

/// The boot under test: the same capacity-1 ready channel main hands it,
/// with the handshake's trust gate pre-answered (`--trust` parity). The
/// child handle rides back too — `kill_on_drop` would tear the scripted
/// server down the moment the helper returned.
async fn boot(
    server: &str,
) -> (
    Arc<Client>,
    vibe_rs::server::ChildHandle,
    tokio::sync::mpsc::Receiver<StartupEvent>,
) {
    let (client, child) = fake_server::spawn(server).await;
    let wire = client.clone();
    let (ready_tx, ready_rx) = tokio::sync::mpsc::channel(1);
    let (trust_tx, trust_rx) = tokio::sync::mpsc::channel::<String>(1);
    drop(trust_tx);
    tokio::spawn(background_boot(
        client,
        ready_tx,
        trust_rx,
        HandshakeParams {
            cwd: None,
            show_unready_config: false,
            resume: StartupResume::None,
            agent_config: AgentConfig {
                trust_workspace: true,
                ..Default::default()
            },
            notifications: None,
            pre_initialized: None,
            trust_already_resolved: false,
        },
    ));
    (wire, child, ready_rx)
}

/// The boot task owns the ready channel end to end: draining runs
/// concurrently with the task (the capacity-1 channel is the real main
/// shape), and the drain returns only when the task ended and dropped its
/// sender — so nothing else can ever arrive after it (the C2 invariant).
async fn drain(mut events: tokio::sync::mpsc::Receiver<StartupEvent>) -> Vec<StartupEvent> {
    let mut seen = Vec::new();
    while let Some(event) = events.recv().await {
        seen.push(event);
    }
    seen
}

async fn count(client: &Arc<Client>, method: &str) -> usize {
    requests(client)
        .await
        .into_iter()
        .filter(|request| request["method"] == method)
        .count()
}

#[tokio::test]
async fn a_keyed_boot_never_calls_setup_status() {
    let (client, _child, events) = boot("boot_keyed_server").await;
    let seen = drain(events).await;
    assert!(
        seen.iter()
            .any(|event| matches!(event, StartupEvent::Ready(_))),
        "the keyed boot reaches Ready"
    );
    assert!(!seen
        .iter()
        .any(|event| matches!(event, StartupEvent::MissingApiKey { .. })));

    assert_eq!(count(&client, "initialize").await, 1);
    assert_eq!(
        count(&client, "setup/status").await,
        0,
        "the probe-positive fast path skips the pre-session status"
    );
    assert_eq!(count(&client, "session/start").await, 1);
}
