//! The onboarding setup scenarios against mocked backends, verified in the
//! wizard's submit-choices payload and the key submit action: the API key
//! screen, the browser sign-in flow (every step, success and failure), and
//! the custom domain in single and dual origin forms. The key persist and
//! the choices landing live server-side now: their wire contract is pinned
//! in `onboarding_fake_server.rs`. The happy-path tenant adoption
//! (`/api/vibe/whoami` over https) is the one path left to real-domain
//! testing: `sanitize_tenant_url` only adopts https origins, so a local
//! http mock can only prove the skip.

#[path = "onboarding_common/keys.rs"]
mod keys;
#[path = "onboarding_common/mock.rs"]
mod mock;
#[path = "onboarding_common/sandbox.rs"]
mod sandbox_mod;
#[path = "onboarding_common/status.rs"]
mod status;

use keys::{down, enter, tab, type_into};
use mock::{SignInMock, SignInScript};
use sandbox_mod::sandbox;
use status::default_status;

use vibe_rs::setup::auth::rpc::SubmitChoices;
use vibe_rs::setup::auth::sign_in_flow::{run_sign_in, SignInEvent, SignInStatus};
use vibe_rs::setup::auth::sign_in_gateway::{SignInErrorCode, SignInGateway};
use vibe_rs::setup::auth::whoami::resolve_tenant_domains;
use vibe_rs::setup::wizard::context::DEFAULT_CONSOLE_BASE_URL;
use vibe_rs::setup::wizard::submit;
use vibe_rs::setup::wizard::{screens, Action, OnboardingState, Screen, ValidationState};

/// AuthMethod with "Use an API key" selected (Down + Enter) lands on the
/// API key screen, like `m` from the browser sign-in screen.
fn open_api_key_screen(state: &mut OnboardingState) {
    state.screen = Screen::AuthMethod;
    screens::handle_key(state, down());
    assert!(matches!(
        screens::handle_key(state, enter()),
        Some(Action::AbortSignIn)
    ));
    assert_eq!(state.screen, Screen::ApiKey);
}

/// SignInTarget with "Other" selected (Down + Enter) lands on the custom
/// domain screen.
fn open_custom_domain(state: &mut OnboardingState) {
    state.screen = Screen::SignInTarget;
    screens::handle_key(state, down());
    screens::handle_key(state, enter());
    assert_eq!(state.screen, Screen::CustomDomain);
}

/// The wizard's completion sequence itself (`submit_choices`'s pre-RPC
/// half): tenant resolution on a non-default console, then the
/// submit-choices payload built from the drifted state.
async fn completion_choices(state: &mut OnboardingState, api_key: &str) -> SubmitChoices {
    if state.context.console_base_url != DEFAULT_CONSOLE_BASE_URL {
        let (provider, vibe) = resolve_tenant_domains(
            &state.context.provider,
            &state.context.console_base_url,
            api_key,
            &state.context.vibe_base_url,
            state.context.enable_system_trust_store,
        )
        .await;
        state.context.provider = provider;
        state.context.vibe_base_url = vibe;
    }
    submit::choices(state)
}

/// The dual-origin wizard state: both fields entered, Enter pressed.
fn dual_domain_state(mock: &SignInMock) -> OnboardingState {
    let mut state = OnboardingState::default();
    open_custom_domain(&mut state);
    type_into(&mut state, &mock.console.url());
    screens::handle_key(&mut state, tab());
    type_into(&mut state, &mock.api.url());
    assert!(matches!(
        screens::handle_key(&mut state, enter()),
        Some(Action::StartBrowserSignIn)
    ));
    assert_eq!(state.screen, Screen::BrowserSignIn);
    state
}

#[test]
fn vibe_theme_env_still_wins_over_the_server_view() {
    let _sandbox = sandbox();
    // Python's context env override: VIBE_THEME wins over the (server-owned)
    // config view the status carries.
    std::env::set_var("VIBE_THEME", "ansi-dark");
    let status = vibe_rs::setup::auth::rpc::SetupStatus {
        theme: "auto".into(),
        ..default_status()
    };
    let mut wizard = OnboardingState::default();
    vibe_rs::setup::wizard::seed(&mut wizard, &status);
    assert_eq!(wizard.context.theme, "ansi-dark");
    std::env::remove_var("VIBE_THEME");
}

// ---------------------------------------------------------------------------
// API key entry
// ---------------------------------------------------------------------------

#[test]
fn api_key_entry_submits_the_typed_secret() {
    let _sandbox = sandbox();
    let mut state = OnboardingState::default();
    open_api_key_screen(&mut state);

    // Empty submit stays on the screen with the validation line.
    assert!(screens::handle_key(&mut state, enter()).is_none());
    assert!(matches!(
        &state.api_key_input.validation,
        ValidationState::Invalid(msg) if msg == "No API key provided."
    ));
    assert_eq!(state.screen, Screen::ApiKey);

    // A typed key submits: the action carries the secret for
    // `setup/store-credential`; nothing persists client-side.
    type_into(&mut state, "sk-mock-key");
    assert!(matches!(
        screens::handle_key(&mut state, enter()),
        Some(Action::SubmitApiKey(key)) if key == "sk-mock-key"
    ));
    assert!(!std::env::var("MISTRAL_API_KEY").is_ok());
}

// ---------------------------------------------------------------------------
// Browser sign-in flow: every step, success and failure
// ---------------------------------------------------------------------------

fn gateway_for(mock: &SignInMock) -> SignInGateway {
    SignInGateway::new(&mock.console.url(), &mock.api.url(), true, false).expect("gateway")
}

/// Drain every event the flow sent until its channel closes.
fn drained(mut rx: tokio::sync::mpsc::Receiver<SignInEvent>) -> Vec<SignInEvent> {
    let mut events = Vec::new();
    loop {
        match rx.try_recv() {
            Ok(event) => events.push(event),
            Err(tokio::sync::mpsc::error::TryRecvError::Disconnected) => return events,
            Err(tokio::sync::mpsc::error::TryRecvError::Empty) => {
                std::thread::sleep(std::time::Duration::from_millis(10));
            }
        }
    }
}

fn statuses(events: &[SignInEvent]) -> Vec<&'static str> {
    events
        .iter()
        .map(|event| match event {
            SignInEvent::Started { .. } => "started",
            SignInEvent::StatusChanged(SignInStatus::OpeningBrowser) => "opening",
            SignInEvent::StatusChanged(SignInStatus::Waiting) => "waiting",
            SignInEvent::StatusChanged(SignInStatus::Exchanging) => "exchanging",
            SignInEvent::StatusChanged(SignInStatus::Completed) => "status-completed",
            SignInEvent::Completed { .. } => "completed",
            SignInEvent::Failed { .. } => "failed",
        })
        .collect()
}

#[tokio::test]
async fn browser_sign_in_walks_every_step_to_completion() {
    let sandbox = sandbox();
    let mock = SignInMock::spawn(SignInScript::default());
    let gateway = gateway_for(&mock);
    let (tx, mut rx) = tokio::sync::mpsc::channel(16);
    let flow = tokio::spawn(async move { run_sign_in(gateway, tx).await });

    // Started carries the console URL; the walk continues through waiting
    // and exchanging to the completed key.
    match rx.recv().await.expect("started") {
        SignInEvent::Started { sign_in_url } => {
            assert!(sign_in_url.starts_with(&mock.console.url()));
        }
        other => panic!("expected Started, got {other:?}"),
    }
    assert!(matches!(
        rx.recv().await,
        Some(SignInEvent::StatusChanged(SignInStatus::OpeningBrowser))
    ));
    assert!(matches!(
        rx.recv().await,
        Some(SignInEvent::StatusChanged(SignInStatus::Waiting))
    ));
    mock.approve();
    assert!(matches!(
        rx.recv().await,
        Some(SignInEvent::StatusChanged(SignInStatus::Exchanging))
    ));
    // Python's success delay shows "Sign-in complete" before the final
    // event: the status first, then the completed key.
    assert!(matches!(
        rx.recv().await,
        Some(SignInEvent::StatusChanged(SignInStatus::Completed))
    ));
    match rx.recv().await.expect("completed") {
        SignInEvent::Completed { api_key } => assert_eq!(api_key, "mock-key-1"),
        other => panic!("expected Completed, got {other:?}"),
    }
    flow.await.expect("join").expect("flow completes");

    // The browser open was recorded, not spawned.
    assert!(sandbox.recorded_opens().iter().any(|entry| entry["url"]
        .as_str()
        .is_some_and(|url| url.starts_with(&mock.console.url()))));
}

#[tokio::test]
async fn browser_sign_in_create_failure_fails_fast() {
    let _sandbox = sandbox();
    let mock = SignInMock::spawn(SignInScript {
        create_status: 500,
        ..Default::default()
    });
    let gateway = gateway_for(&mock);
    let (tx, rx) = tokio::sync::mpsc::channel(16);
    let error = run_sign_in(gateway, tx).await.expect_err("create fails");
    assert_eq!(error.code, SignInErrorCode::StartFailed);
    // The failure came before the URL: nothing was sent, no page opened.
    assert_eq!(statuses(&drained(rx)), Vec::<&str>::new());
    assert!(!mock.is_approved());
}

#[tokio::test]
async fn browser_sign_in_poll_statuses_fail_with_real_messages() {
    let _sandbox = sandbox();
    for (status, message) in [
        ("expired", "Browser sign-in expired."),
        ("denied", "Browser sign-in was denied."),
        ("error", "Console says no."),
    ] {
        let mock = SignInMock::spawn(SignInScript {
            poll_status: Some(status),
            ..Default::default()
        });
        let (tx, rx) = tokio::sync::mpsc::channel(16);
        let error = run_sign_in(gateway_for(&mock), tx)
            .await
            .expect_err("poll status fails the flow");
        assert_eq!(error.message, message, "status {status}");
        // The walk reached waiting, but no key ever arrived.
        assert_eq!(
            statuses(&drained(rx)),
            ["started", "opening", "waiting"],
            "status {status}"
        );
    }
}

#[tokio::test]
async fn browser_sign_in_exchange_failure_returns_exchange_failed() {
    let _sandbox = sandbox();
    let mock = SignInMock::spawn(SignInScript {
        exchange_status: 500,
        ..Default::default()
    });
    mock.approve();
    let (tx, rx) = tokio::sync::mpsc::channel(16);
    let error = run_sign_in(gateway_for(&mock), tx)
        .await
        .expect_err("exchange fails");
    assert_eq!(error.code, SignInErrorCode::ExchangeFailed);
    // The walk reached exchanging, but no completion was sent.
    assert_eq!(
        statuses(&drained(rx)),
        ["started", "opening", "waiting", "exchanging"]
    );
}

// ---------------------------------------------------------------------------
// Custom domain, single origin
// ---------------------------------------------------------------------------

#[tokio::test]
async fn single_custom_domain_derives_submits_and_round_trips() {
    let _sandbox = sandbox();
    let mock = SignInMock::spawn(SignInScript::default());
    let mut state = OnboardingState::default();
    open_custom_domain(&mut state);

    type_into(&mut state, &mock.console.url());
    assert!(matches!(
        screens::handle_key(&mut state, enter()),
        Some(Action::StartBrowserSignIn)
    ));

    // Single-origin derivation: the API base is the console origin's own
    // `/api`, no cross-origin rewrite, no account-base move.
    assert_eq!(state.screen, Screen::BrowserSignIn);
    assert_eq!(
        state.context.provider.browser_auth_api_base_url,
        Some(format!("{}/api", mock.console.url()))
    );
    assert!(!state.context.provider.browser_auth_allow_origin_rewrite);
    assert_eq!(state.context.console_base_url, mock.console.url());

    let choices = completion_choices(&mut state, "sk-mock-key").await;
    // The payload carries the drifted console URL and the provider view,
    // which the app-server merges onto the resolved provider.
    assert_eq!(
        choices.console_base_url.as_deref(),
        Some(mock.console.url().as_str())
    );
    let provider = choices.provider.expect("provider on drift");
    assert_eq!(provider.browser_auth_base_url, Some(mock.console.url()));
    assert_eq!(provider.api_key_env_var, "MISTRAL_API_KEY");
}

#[test]
fn custom_domain_input_failures_block_submit_with_per_field_feedback() {
    let _sandbox = sandbox();
    let mut state = OnboardingState::default();
    open_custom_domain(&mut state);

    type_into(&mut state, "not a url");
    assert!(screens::handle_key(&mut state, enter()).is_none());
    assert!(matches!(
        &state.custom_domain.validation,
        ValidationState::Invalid(msg) if msg == "Enter a valid domain URL."
    ));
    assert_eq!(state.screen, Screen::CustomDomain);

    // A valid console field with an invalid API field is equally blocked.
    type_into(&mut state, "http://console.example");
    screens::handle_key(&mut state, tab());
    type_into(&mut state, "still not");
    assert!(screens::handle_key(&mut state, enter()).is_none());
    assert!(matches!(
        &state.custom_domain_api.validation,
        ValidationState::Invalid(msg) if msg == "Enter a valid API base URL."
    ));
}

#[tokio::test]
async fn single_custom_domain_flow_completes_against_one_origin() {
    let _sandbox = sandbox();
    let mock = SignInMock::spawn(SignInScript::default());
    // The wizard's derived single-domain API base: `{console}/api` — the
    // console origin itself serves the sign-in endpoints under that path.
    let gateway = SignInGateway::new(
        &mock.console.url(),
        &format!("{}/api", mock.console.url()),
        false,
        false,
    )
    .expect("gateway");
    let process = gateway
        .create_process("challenge")
        .await
        .expect("create process");
    assert!(process.sign_in_url.starts_with(&mock.console.url()));
    assert!(process
        .poll_url
        .starts_with(&format!("{}/api", mock.console.url())));
    mock.approve();
    let completed = gateway.poll(&process.poll_url).await.expect("poll");
    assert_eq!(completed.status, "completed");
    let api_key = gateway
        .exchange(&process.process_id, "tok-1", "verifier")
        .await
        .expect("exchange");
    assert_eq!(api_key, "mock-key-1");
}

// ---------------------------------------------------------------------------
// Custom domain, dual origin
// ---------------------------------------------------------------------------

#[tokio::test]
async fn dual_custom_domain_submits_the_split_pair() {
    let _sandbox = sandbox();
    let mock = SignInMock::spawn(SignInScript::default());
    let mut state = dual_domain_state(&mock);

    // Split-horizon derivation: both browser-auth URLs, the rewrite flag,
    // the account base moved onto the API origin.
    assert_eq!(
        state.context.provider.browser_auth_base_url,
        Some(mock.console.url())
    );
    assert_eq!(
        state.context.provider.browser_auth_api_base_url,
        Some(mock.api.url())
    );
    assert!(state.context.provider.browser_auth_allow_origin_rewrite);
    assert_eq!(state.context.console_base_url, mock.api.url());

    let choices = completion_choices(&mut state, "mock-key-1").await;
    // The payload keeps the split: the account console on the API origin.
    let provider = choices.provider.expect("provider on drift");
    assert_eq!(provider.browser_auth_base_url, Some(mock.console.url()));
    assert_eq!(provider.browser_auth_api_base_url, Some(mock.api.url()));
    assert!(provider.browser_auth_allow_origin_rewrite);
    assert_eq!(
        choices.console_base_url.as_deref(),
        Some(mock.api.url().as_str())
    );
}

#[tokio::test]
async fn dual_custom_domain_completion_keeps_entered_values_without_https_tenants() {
    let _sandbox = sandbox();
    let mock = SignInMock::spawn(SignInScript {
        whoami: Some(
            "{\"api_base\": \"http://tenant.api.example\", \
             \"vibe_base\": \"http://tenant.vibe.example\"}",
        ),
        ..Default::default()
    });
    let mut state = dual_domain_state(&mock);

    // The completion sequence asks whoami on the account base; the mock's
    // http tenant URLs are rejected, so the entered split pair survives.
    let _choices = completion_choices(&mut state, "mock-key-1").await;
    assert_eq!(
        state.context.provider.browser_auth_base_url,
        Some(mock.console.url())
    );
    assert_eq!(
        state.context.provider.browser_auth_api_base_url,
        Some(mock.api.url())
    );
    assert_eq!(state.context.console_base_url, mock.api.url());
}

#[tokio::test]
async fn dual_custom_domain_completion_survives_a_failing_whoami() {
    let _sandbox = sandbox();
    // No whoami route: 404 on the account base.
    let mock = SignInMock::spawn(SignInScript::default());
    let mut state = dual_domain_state(&mock);

    let _choices = completion_choices(&mut state, "mock-key-1").await;
    assert_eq!(state.context.console_base_url, mock.api.url());
    assert_eq!(
        state.context.provider.browser_auth_api_base_url,
        Some(mock.api.url())
    );
}
