//! OIDC lifecycle and RPC wire tests with an isolated fake app-server.

use std::sync::Arc;
use std::time::Duration;

use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
use serde_json::{json, Value};
use tokio::time::timeout;
use vibe_rs::server::Client;
use vibe_rs::setup::auth::browser_sign_in::SignInHandle;
use vibe_rs::setup::auth::oidc;
use vibe_rs::setup::auth::sign_in_flow::{SignInEvent, SignInStatus};
use vibe_rs::setup::wizard::{actions, screens, submit, Action, OnboardingState, Screen};

use crate::onboarding_common::{fake_server, sandbox};

#[test]
#[ignore]
fn server_waiting() {
    let polls = std::cell::Cell::new(0);
    fake_server::serve_scripted(|method, _| {
        Ok(match method {
            "setup/oidc-start" => {
                json!({"attemptId": "attempt"})
            }
            "setup/oidc-poll" => {
                polls.set(polls.get() + 1);
                let url = (polls.get() > 2).then_some("https://auth.mistral.ai/authorize");
                json!({"status": "waiting", "authorizationUrl": url})
            }
            "setup/submit-choices" => json!({"outcome": "completed"}),
            _ => json!({}),
        })
    });
}

#[test]
#[ignore]
fn server_slow_start() {
    fake_server::serve_scripted(|method, _| {
        if method == "setup/oidc-start" {
            std::thread::sleep(Duration::from_secs(6));
            return Ok(json!({"attemptId": "late"}));
        }
        Ok(json!({}))
    });
}

#[test]
#[ignore]
fn server_completed() {
    let polls = std::cell::Cell::new(0);
    fake_server::serve_scripted(|method, _| {
        Ok(match method {
            "setup/oidc-start" => {
                json!({"attemptId": "attempt"})
            }
            "setup/oidc-poll" => {
                polls.set(polls.get() + 1);
                json!({"status": if polls.get() == 1 { "exchanging" } else { "completed" },
                    "authorizationUrl": "https://auth.mistral.ai/authorize"})
            }
            _ => json!({}),
        })
    });
}

async fn waiting(handle: &mut SignInHandle) {
    timeout(Duration::from_secs(2), async {
        loop {
            if matches!(
                handle.recv().await,
                Some(SignInEvent::StatusChanged(SignInStatus::Waiting))
            ) {
                break;
            }
        }
    })
    .await
    .unwrap();
}

async fn cancelled(client: &Client) -> Vec<Value> {
    timeout(Duration::from_secs(8), async {
        loop {
            let requests = fake_server::requests(client).await;
            if requests
                .iter()
                .any(|request| request["method"] == oidc::SETUP_OIDC_CANCEL)
            {
                return requests;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap()
}

#[tokio::test]
async fn abort_cancels_the_server_attempt() {
    let _sandbox = sandbox::sandbox();
    let (client, _child) = fake_server::spawn(&test_name!("server_waiting")).await;
    let mut handle = oidc::spawn(client.clone());
    waiting(&mut handle).await;
    handle.abort();
    let requests = cancelled(&client).await;
    assert_eq!(
        requests.last().unwrap()["params"],
        json!({"attemptId": "attempt"})
    );
}

#[tokio::test]
async fn dropping_the_handle_cancels_the_server_attempt() {
    let _sandbox = sandbox::sandbox();
    let (client, _child) = fake_server::spawn(&test_name!("server_waiting")).await;
    let mut handle = oidc::spawn(client.clone());
    waiting(&mut handle).await;
    drop(handle);
    cancelled(&client).await;
}

#[tokio::test]
async fn cancelling_during_start_preserves_attempt_id_beyond_five_seconds() {
    let sandbox = sandbox::sandbox();
    let (client, _child) = fake_server::spawn(&test_name!("server_slow_start")).await;
    let handle = oidc::spawn(client.clone());
    tokio::time::sleep(Duration::from_millis(50)).await;
    handle.abort();
    let requests = cancelled(&client).await;
    assert_eq!(
        requests.last().unwrap()["params"],
        json!({"attemptId": "late"})
    );
    assert!(sandbox.recorded_opens().is_empty());
}

#[tokio::test]
async fn completed_status_dwells_then_emits_credential_free_completion() {
    let _sandbox = sandbox::sandbox();
    let (client, _child) = fake_server::spawn(&test_name!("server_completed")).await;
    let mut handle = oidc::spawn(client.clone());
    waiting(&mut handle).await;
    assert!(matches!(
        handle.recv().await,
        Some(SignInEvent::StatusChanged(SignInStatus::Exchanging))
    ));
    assert!(matches!(
        timeout(Duration::from_secs(2), handle.recv())
            .await
            .unwrap(),
        Some(SignInEvent::StatusChanged(SignInStatus::Completed))
    ));
    let completed_at = tokio::time::Instant::now();
    assert!(matches!(
        timeout(Duration::from_secs(3), handle.recv())
            .await
            .unwrap(),
        Some(SignInEvent::OidcCompleted)
    ));
    assert!(completed_at.elapsed() >= Duration::from_secs(2));
    drop(handle);
    let requests = fake_server::requests(&client).await;
    assert_eq!(
        requests,
        vec![
            json!({"method": "setup/oidc-start", "params": {}}),
            json!({"method": "setup/oidc-poll", "params": {"attemptId": "attempt"}}),
            json!({"method": "setup/oidc-poll", "params": {"attemptId": "attempt"}}),
        ]
    );
}

#[tokio::test]
async fn complete_oidc_submits_choices_without_changing_the_gateway() {
    let _sandbox = sandbox::sandbox();
    let (client, _child) = fake_server::spawn(&test_name!("server_waiting")).await;
    let mut wizard = OnboardingState {
        open: true,
        theme_selected: true,
        screen: Screen::SignInTarget,
        ..Default::default()
    };
    wizard.context.oidc_enabled = true;
    wizard.context.provider.name = "internal-ai-gateway".into();
    wizard.context.provider.api_base = "https://gateway.invalid".into();
    wizard.context.provider.browser_auth_base_url = None;
    wizard.context.provider.browser_auth_api_base_url = None;
    wizard.context.console_base_url = "https://console.gateway.invalid".into();
    wizard.context.vibe_base_url = "https://chat.gateway.invalid".into();
    wizard.config = wizard.context.clone();
    let expected = serde_json::to_value(submit::choices(&wizard)).unwrap();
    assert!(matches!(
        screens::handle_key(
            &mut wizard,
            KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)
        ),
        Some(Action::StartBrowserSignIn)
    ));
    assert_eq!(wizard.context, wizard.config);
    let close =
        actions::handle_onboarding_action(&mut wizard, &mut None, &client, Action::CompleteOidc)
            .await;
    assert!(matches!(close, Some(actions::Close::Completed { .. })));
    let requests = fake_server::requests(&client).await;
    assert_eq!(requests.len(), 1);
    assert_eq!(requests[0]["method"], "setup/submit-choices");
    assert_eq!(requests[0]["params"], expected);
    assert_eq!(wizard.context, wizard.config);
    assert!(!wizard.open);
}

#[tokio::test]
async fn transport_failure_stays_an_error_without_a_legacy_fallback() {
    let mut handle = oidc::spawn(Arc::new(Client::stub()));
    assert!(matches!(
        timeout(Duration::from_secs(1), handle.recv())
            .await
            .unwrap(),
        Some(SignInEvent::Failed { .. })
    ));
    assert!(handle.recv().await.is_none());
}

#[tokio::test]
async fn discovery_is_silent_then_opens_once_and_waits_immediately() {
    let sandbox = sandbox::sandbox();
    let (client, _child) = fake_server::spawn(&test_name!("server_waiting")).await;
    let mut handle = oidc::spawn(client.clone());
    assert!(timeout(Duration::from_millis(300), handle.recv())
        .await
        .is_err());
    assert!(sandbox.recorded_opens().is_empty());
    assert!(matches!(
        handle.recv().await,
        Some(SignInEvent::Started { .. })
    ));
    assert!(matches!(
        handle.recv().await,
        Some(SignInEvent::StatusChanged(SignInStatus::OpeningBrowser))
    ));
    assert!(matches!(
        handle.recv().await,
        Some(SignInEvent::StatusChanged(SignInStatus::Waiting))
    ));
    assert!(timeout(Duration::from_millis(600), handle.recv())
        .await
        .is_err());
    assert_eq!(sandbox.recorded_opens().len(), 1);
    handle.abort();
    cancelled(&client).await;
}

#[tokio::test]
async fn cancelling_during_discovery_never_opens_a_browser() {
    let sandbox = sandbox::sandbox();
    let (client, _child) = fake_server::spawn(&test_name!("server_waiting")).await;
    let handle = oidc::spawn(client.clone());
    timeout(Duration::from_secs(2), async {
        while fake_server::params_of(&client, oidc::SETUP_OIDC_POLL)
            .await
            .is_empty()
        {
            tokio::task::yield_now().await;
        }
    })
    .await
    .unwrap();
    drop(handle);
    let requests = cancelled(&client).await;
    assert_eq!(
        requests.last().unwrap()["params"],
        json!({"attemptId": "attempt"})
    );
    assert!(sandbox.recorded_opens().is_empty());
}
