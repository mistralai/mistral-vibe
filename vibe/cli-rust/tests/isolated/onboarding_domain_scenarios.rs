//! Custom-domain onboarding scenarios against the legacy browser-auth backend.

use super::*;

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
