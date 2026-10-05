//! The dual custom-domain contract against live sockets: the gateway's
//! create/poll/exchange wire shapes, the wizard's Enter composition (the
//! split-horizon derivation), and the whoami tenant-adoption skip. The
//! wider setup matrix (API key, single domain, per-step failures) lives in
//! `onboarding_setup_scenarios.rs`; both share `onboarding_common`'s mock.

#[path = "onboarding_common/keys.rs"]
mod keys;
#[path = "onboarding_common/mock.rs"]
mod mock;

use keys::{down, enter, tab, type_into};
use mock::{SignInMock, SignInScript};

use vibe_rs::setup::auth::rpc::ProviderView;
use vibe_rs::setup::auth::sign_in_gateway::{SignInErrorCode, SignInGateway};
use vibe_rs::setup::auth::whoami::resolve_tenant_domains;
use vibe_rs::setup::wizard::{screens, Action, OnboardingState, Screen};

#[tokio::test]
async fn the_gateway_speaks_the_dual_origin_contract() {
    let mock = SignInMock::spawn(SignInScript::default());
    let gateway =
        SignInGateway::new(&mock.console.url(), &mock.api.url(), true, false).expect("gateway");

    let process = gateway
        .create_process("challenge")
        .await
        .expect("create process");
    assert_eq!(process.process_id, "proc-1");
    // The sign-in URL lives on the console origin; the poll URL on the API
    // origin — the split-horizon pair the dual inputs configure.
    assert!(process.sign_in_url.starts_with(&mock.console.url()));
    assert!(process.poll_url.starts_with(&mock.api.url()));

    let pending = gateway.poll(&process.poll_url).await.expect("pending poll");
    assert_eq!(pending.status, "pending");
    assert!(pending.exchange_token.is_none());

    // Approving (visiting the sign-in page) flips the next poll.
    mock.approve();
    assert!(mock.is_approved());
    let completed = gateway
        .poll(&process.poll_url)
        .await
        .expect("completed poll");
    assert_eq!(completed.status, "completed");
    assert_eq!(completed.exchange_token.as_deref(), Some("tok-1"));

    let api_key = gateway
        .exchange(&process.process_id, "tok-1", "verifier")
        .await
        .expect("exchange");
    assert_eq!(api_key, "mock-key-1");
}

#[tokio::test]
async fn a_foreign_sign_in_url_fails_without_the_rewrite_flag() {
    let mock = SignInMock::spawn(SignInScript {
        foreign_sign_in_url: true,
        ..Default::default()
    });
    // Single-origin setup (rewrite off): the mock serves the sign-in URL on
    // its API origin, foreign to the configured console base, so create
    // fails and the URL is never opened in a browser.
    let gateway =
        SignInGateway::new(&mock.console.url(), &mock.api.url(), false, false).expect("gateway");
    let error = gateway
        .create_process("challenge")
        .await
        .expect_err("rejected");
    assert_eq!(error.code, SignInErrorCode::StartFailed);
    assert!(error.message.contains("origin validation"));
}

#[test]
fn enter_with_both_fields_derives_the_split_horizon_pair() {
    let mock = SignInMock::spawn(SignInScript::default());
    let mut state = OnboardingState {
        screen: Screen::SignInTarget,
        ..OnboardingState::default()
    };
    // Target: choose "Other" (Down + Enter) to reach the custom domain screen.
    screens::handle_key(&mut state, down());
    screens::handle_key(&mut state, enter());
    assert_eq!(state.screen, Screen::CustomDomain);

    type_into(&mut state, &mock.console.url());
    screens::handle_key(&mut state, tab());
    type_into(&mut state, &mock.api.url());
    let action = screens::handle_key(&mut state, enter());

    // The Enter composition (Python `resolve_browser_auth_urls`): both
    // browser-auth URLs, the cross-origin rewrite flag, the account base
    // moved onto the API origin, and the flow handed to the sign-in screen.
    assert!(matches!(action, Some(Action::StartBrowserSignIn)));
    assert_eq!(state.screen, Screen::BrowserSignIn);
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
}

#[tokio::test]
async fn an_http_tenant_url_is_not_adopted_by_whoami() {
    let mock = SignInMock::spawn(SignInScript {
        whoami: Some(
            "{\"api_base\": \"http://tenant.api.example\", \
             \"vibe_base\": \"http://tenant.vibe.example\"}",
        ),
        ..Default::default()
    });
    let provider = ProviderView {
        name: "mistral".into(),
        api_base: "https://api.entered.example/v1".into(),
        api_key_env_var: "MISTRAL_API_KEY".into(),
        browser_auth_base_url: None,
        browser_auth_api_base_url: None,
        browser_auth_allow_origin_rewrite: false,
    };
    // The whoami route advertises http tenant URLs; sanitize rejects them,
    // so the entered provider and chat base come back unchanged.
    let (provider, vibe_base) = resolve_tenant_domains(
        &provider,
        &mock.api.url(),
        "mock-key-1",
        "https://chat.entered.example",
        false,
    )
    .await;
    assert_eq!(provider.api_base, "https://api.entered.example/v1");
    assert_eq!(vibe_base, "https://chat.entered.example");
}
