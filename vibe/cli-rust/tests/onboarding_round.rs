//! The missing-key round's reachable seams against scripted app-servers: the
//! live handshake's typed missing-key verdict (the event the steady loop
//! matches to unwind with `LoopExit::NeedsOnboarding`), and the one-child
//! contract — the wizard round initializes its child once, and the retried
//! handshake never re-initializes it (the app-server rejects a second
//! `initialize`, so the reuse is pinned on the wire). The full round —
//! `EventLoop` unwinding, the wizard drawing on the live terminal,
//! re-entry on the round's child — owns the real stdout
//! (`terminal::init`; `TerminalGuard` has no outside constructor), so it
//! stays with the client-e2e scenarios; these pin its typed inputs.

#[allow(dead_code)]
#[path = "onboarding_common/fake_server.rs"]
mod fake_server;

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use serde_json::json;
use vibe_rs::cli::StartupResume;
use vibe_rs::event_handler::apply_startup_event;
use vibe_rs::server::{AgentConfig, Client};
use vibe_rs::setup::wizard::actions::{handle_onboarding_action, Close};
use vibe_rs::setup::wizard::{Action, OnboardingState};
use vibe_rs::startup::{handshake, initialize_connection, HandshakeParams, StartupEvent};

use fake_server::requests;

/// Which `session/start` answer the round's scripted server gives.
enum RoundScript {
    /// The missing-API-key error the first handshake fails on.
    MissingKey,
    /// A successful start, so the child reaches its `Ready`.
    Ready,
}

#[test]
#[ignore]
fn round_missing_key_handshake() {
    serve_round(RoundScript::MissingKey);
}

#[test]
#[ignore]
fn round_ready_handshake() {
    serve_round(RoundScript::Ready);
}

/// The round's scripted conversation: the handshake's methods answered per
/// script (the missing-key verdict fails `session/start`), through the
/// shared stdin loop the setup fake serves its own scripts with.
fn serve_round(script: RoundScript) {
    fake_server::serve_scripted(move |method, _msg| match (&script, method) {
        (RoundScript::MissingKey, "session/start") => Err(json!({
            "code": "unauthorized",
            "message": "missing API key",
            "data": {"provider": "mistral", "env_key": "MISTRAL_API_KEY"},
        })),
        (_, "initialize") => Ok(json!({"serverInfo": {"version": "test"}})),
        (_, "session/start") => Ok(json!({
            "state": {"eventId": 1, "session": {"id": "session-7"}},
        })),
        (_, "runtime/read") => Ok(json!({
            "ready": true,
            "runtime": {
                "config": {
                    "activeModel": {"displayName": "Test", "thinking": "off"},
                    "theme": "dark",
                },
            },
        })),
        (_, "config/read") => Ok(json!({"config": {}})),
        _ => Ok(json!({})),
    });
}

/// The handshake `onboarding_round`'s retry re-runs: a plain attach, no
/// resume, trust granted (`--trust` parity: the gate stays answered).
fn run_handshake(
    client: Arc<Client>,
    server_version: Option<String>,
) -> tokio::sync::mpsc::Receiver<StartupEvent> {
    let (event_tx, event_rx) = tokio::sync::mpsc::channel(16);
    let (trust_tx, trust_rx) = tokio::sync::mpsc::channel::<String>(1);
    drop(trust_tx);
    tokio::spawn(handshake(
        client,
        event_tx,
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
            pre_initialized: server_version,
            trust_already_resolved: false,
        },
    ));
    event_rx
}

#[tokio::test]
async fn the_live_missing_key_verdict_is_typed_and_names_the_provider() {
    let (client, _child) = fake_server::spawn("round_missing_key_handshake").await;
    let mut events = run_handshake(client.clone(), None);

    // The verdict the steady loop matches to unwind with
    // `LoopExit::NeedsOnboarding`: the server's provider name (the env var
    // is derived server-side at `setup/store-credential` now).
    let StartupEvent::MissingApiKey { provider, env_key } = events.recv().await.expect("verdict")
    else {
        panic!("expected MissingApiKey, got another startup event");
    };
    assert_eq!(provider, "mistral");
    assert_eq!(env_key.as_deref(), Some("MISTRAL_API_KEY"));
    // The startup is over for this run: no Ready ever follows the verdict.
    assert!(events.recv().await.is_none());

    // The reducer itself never touches wizard state: the loop raises the
    // typed exit instead, so the run must continue past the event.
    let mut app = vibe_rs::app::App::default();
    let (config_tx, _config_rx) = tokio::sync::mpsc::channel(1);
    assert!(!apply_startup_event(
        &mut app,
        &client,
        &config_tx,
        StartupEvent::MissingApiKey {
            provider: "mistral".into(),
            env_key: Some("MISTRAL_API_KEY".into()),
        },
    ));
}

#[tokio::test]
async fn the_rounds_child_is_initialized_once_and_serves_the_session() {
    let (client, _child) = fake_server::spawn("round_ready_handshake").await;

    // The wizard round's pre-wizard step: initialize once, before `setup/*`.
    let server_version = initialize_connection(&client).await.expect("initialize");
    assert_eq!(server_version, "test");

    // The retried handshake reuses the SAME connection: `initialize` may
    // only be called once server-side, so the handshake skips it and the
    // child still reaches `Ready` — one child per setup round.
    let mut events = run_handshake(client.clone(), Some(server_version));
    let mut saw_ready = false;
    while let Some(event) = events.recv().await {
        assert!(
            !matches!(event, StartupEvent::MissingApiKey { .. }),
            "the reused child must authenticate through the server's env"
        );
        saw_ready |= matches!(event, StartupEvent::Ready(_));
    }
    assert!(saw_ready, "the reused child reached Ready");

    // Exactly one initialize frame ever left the client.
    let initializes = requests(&client)
        .await
        .into_iter()
        .filter(|request| request["method"] == "initialize")
        .count();
    assert_eq!(
        initializes, 1,
        "the round's child is initialized exactly once"
    );
}

// ---------------------------------------------------------------------------
// Overlay end-to-end: MissingApiKey -> persist -> retry handshake -> Ready
// ---------------------------------------------------------------------------

/// The fake server's `session/start` counter (the child process starts fresh,
/// so the first call is the missing-key handshake, the second is the retry).
static SESSION_START_COUNT: AtomicUsize = AtomicUsize::new(0);

/// The overlay e2e script: the first `session/start` fails with the typed
/// missing-key verdict, `setup/store-credential` and `setup/submit-choices`
/// complete, and the second `session/start` (the retry handshake on the same
/// child) reaches Ready — the server's own `os.environ` mutation is what
/// makes the persisted key visible.
#[test]
#[ignore]
fn round_overlay_e2e() {
    fake_server::serve_scripted(|method, _msg| match method {
        "session/start" => {
            if SESSION_START_COUNT.fetch_add(1, Ordering::SeqCst) == 0 {
                Err(json!({
                    "code": "unauthorized",
                    "message": "missing API key",
                    "data": {"provider": "mistral", "env_key": "MISTRAL_API_KEY"},
                }))
            } else {
                Ok(json!({
                    "state": {"eventId": 1, "session": {"id": "session-7"}},
                }))
            }
        }
        "initialize" => Ok(json!({"serverInfo": {"version": "test"}})),
        "runtime/read" => Ok(json!({
            "ready": true,
            "runtime": {
                "config": {
                    "activeModel": {"displayName": "Test", "thinking": "off"},
                    "theme": "dark",
                },
            },
        })),
        "config/read" => Ok(json!({})),
        "setup/store-credential" => Ok(json!({"outcome": "completed"})),
        "setup/submit-choices" => Ok(json!({"outcome": "completed", "failures": []})),
        _ => Ok(json!({})),
    });
}

/// The overlay path end to end at the wire level: the first handshake's typed
/// missing-key verdict, the wizard's persist (`setup/store-credential` +
/// `setup/submit-choices`) on the same child, and the retry handshake reaching
/// `Ready` — one child, one initialize, the full `MissingApiKey` → persist →
/// respawn → `Ready` flow that mgesbert requested (the TUI-level round stays
/// with the client-e2e scenarios).
#[tokio::test]
async fn the_overlay_persist_then_retry_reaches_ready() {
    let (client, _child) = fake_server::spawn("round_overlay_e2e").await;

    // The wizard round's pre-wizard step: initialize once.
    let server_version = initialize_connection(&client).await.expect("initialize");
    assert_eq!(server_version, "test");

    // First handshake: the typed missing-key verdict.
    let mut events = run_handshake(client.clone(), Some(server_version.clone()));
    let StartupEvent::MissingApiKey { provider, env_key } = events.recv().await.expect("verdict")
    else {
        panic!("expected MissingApiKey, got another startup event");
    };
    assert_eq!(provider, "mistral");
    assert_eq!(env_key.as_deref(), Some("MISTRAL_API_KEY"));
    assert!(events.recv().await.is_none(), "the startup is over");

    // The wizard's persist: store-credential + submit-choices on the SAME
    // child (the server's os.environ mutation makes the key visible).
    let mut wizard = OnboardingState::default();
    let mut sign_in = None;
    let close = handle_onboarding_action(
        &mut wizard,
        &mut sign_in,
        &client,
        Action::SubmitApiKey("sk-mock-key".into()),
    )
    .await;
    assert!(
        matches!(close, Some(Close::Completed { .. })),
        "the wizard's persist completes: {close:?}"
    );

    // The retry handshake on the SAME child: the server now has the key.
    let mut events = run_handshake(client.clone(), Some(server_version));
    let mut saw_ready = false;
    while let Some(event) = events.recv().await {
        assert!(
            !matches!(event, StartupEvent::MissingApiKey { .. }),
            "the persisted key satisfied the server"
        );
        saw_ready |= matches!(event, StartupEvent::Ready(_));
    }
    assert!(saw_ready, "the retry handshake reached Ready");

    // The wire log pins the full flow: session/start (fail), store-credential,
    // submit-choices, session/start (success) — one child, one initialize.
    let all_requests = requests(&client).await;
    let methods: Vec<&str> = all_requests
        .iter()
        .map(|r| r["method"].as_str().unwrap_or_default())
        .collect();
    assert_eq!(
        methods,
        vec![
            "initialize",
            "session/start",
            "setup/store-credential",
            "setup/submit-choices",
            "session/start",
            "runtime/read",
            "config/read",
        ],
        "the full overlay flow on one child"
    );
}
