//! A server that dies during `session/start` is a reported client bug; a server refusal is not.

use crate::onboarding_common::fake_server;

use std::sync::{Arc, Mutex};

use serde_json::json;
use tracing::{Event, Subscriber};
use tracing_subscriber::layer::{Context, Layer, SubscriberExt as _};
use vibe_rs::cli::StartupResume;
use vibe_rs::observability::logging::reports_to_sentry;
use vibe_rs::server::AgentConfig;
use vibe_rs::startup::{handshake, HandshakeParams};

#[test]
#[ignore]
fn server_dies_on_session_start() {
    fake_server::serve_scripted(|method, _msg| match method {
        "session/start" => std::process::exit(0),
        _ => Ok(json!({"serverInfo": {"version": "test"}})),
    });
}

#[test]
#[ignore]
fn server_refuses_session_start() {
    fake_server::serve_scripted(|method, _msg| match method {
        "session/start" => Err(json!({"code": "internal_error", "message": "MCP server failed"})),
        _ => Ok(json!({"serverInfo": {"version": "test"}})),
    });
}

struct Reported(Arc<Mutex<Vec<bool>>>);

impl<S: Subscriber> Layer<S> for Reported {
    fn on_event(&self, event: &Event<'_>, _ctx: Context<'_, S>) {
        if *event.metadata().level() == tracing::Level::ERROR {
            self.0
                .lock()
                .unwrap()
                .push(reports_to_sentry(event.metadata()));
        }
    }
}

/// Whether each ERROR record the failed handshake logged goes to Sentry.
async fn startup_reports(server: &str) -> Vec<bool> {
    let (client, _child) = fake_server::spawn(&test_name!(server)).await;
    let (events_tx, _events_rx) = tokio::sync::mpsc::channel(16);
    let (_trust_tx, trust_rx) = tokio::sync::mpsc::channel::<String>(1);
    let reported = Arc::new(Mutex::new(Vec::new()));
    let _guard = tracing::subscriber::set_default(
        tracing_subscriber::registry().with(Reported(reported.clone())),
    );
    let params = HandshakeParams {
        cwd: None,
        show_unready_config: false,
        resume: StartupResume::None,
        agent_config: AgentConfig {
            trust_workspace: true,
            ..Default::default()
        },
        notifications: None,
        pre_initialized: None,
        trust_already_resolved: true,
    };
    handshake(client, events_tx, trust_rx, params).await;
    let reported = reported.lock().unwrap().clone();
    reported
}

#[tokio::test]
async fn a_server_dying_on_session_start_is_reported() {
    assert_eq!(
        startup_reports("server_dies_on_session_start").await,
        vec![true]
    );
}

#[tokio::test]
async fn a_server_refusing_session_start_is_not_reported() {
    assert_eq!(
        startup_reports("server_refuses_session_start").await,
        vec![false]
    );
}
