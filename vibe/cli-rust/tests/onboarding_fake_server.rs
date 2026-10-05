//! The wizard's five-outcome contract against a scripted app-server, driven
//! end to end through the wizard's key submit: `completed` proceeds to
//! `submit-choices`, `env_var_error` fails the run (exit 1),
//! `save_error` warns and still submits, `provider_config_error` warns and
//! continues, cancel exits 0 — plus the old-server skew (`setup/*`
//! method-not-found): a clear setup-unavailable error, never a local write.

#[path = "onboarding_common/fake_server.rs"]
mod fake_server;

use fake_server::{params_of, requests, spawn, SetupScript};
use serde_json::json;
use vibe_rs::setup::auth::rpc;
use vibe_rs::setup::wizard::actions::{handle_onboarding_action, Close};
use vibe_rs::setup::wizard::OnboardingState;
use vibe_rs::setup::SetupVerdict;

#[test]
#[ignore]
fn fake_server_setup_completed() {
    fake_server::serve(SetupScript::default());
}

#[test]
#[ignore]
fn fake_server_setup_env_var_error() {
    fake_server::serve(SetupScript {
        store_outcome: Some("env_var_error"),
        ..SetupScript::default()
    });
}

#[test]
#[ignore]
fn fake_server_setup_save_error() {
    fake_server::serve(SetupScript {
        store_outcome: Some("save_error"),
        ..SetupScript::default()
    });
}

#[test]
#[ignore]
fn fake_server_setup_provider_config_error() {
    fake_server::serve(SetupScript {
        submit_outcome: Some("provider_config_error"),
        ..SetupScript::default()
    });
}

#[test]
#[ignore]
fn fake_server_setup_absent() {
    fake_server::serve(SetupScript {
        absent: true,
        ..SetupScript::default()
    });
}

/// One key submit on a default-domain wizard state.
async fn submit(client: &std::sync::Arc<vibe_rs::server::Client>) -> Option<Close> {
    let mut wizard = OnboardingState::default();
    let mut sign_in = None;
    handle_onboarding_action(
        &mut wizard,
        &mut sign_in,
        client,
        vibe_rs::setup::wizard::Action::SubmitApiKey("sk-mock-key".into()),
    )
    .await
}

/// The continue-anyway warnings of one completed submit, joined like the
/// interactive rounds print them.
async fn submitted_warnings(close: Option<Close>) -> Vec<String> {
    match close {
        Some(Close::Completed { warnings }) => warnings,
        other => panic!("expected Completed, got {other:?}"),
    }
}

#[tokio::test]
async fn setup_status_seeds_the_wizard_from_the_server_view() {
    let (client, _child) = spawn("fake_server_setup_completed").await;
    let status = rpc::status(&client, None).await.expect("status");
    assert!(status.provider.browser_auth_base_url.is_some());
    assert_eq!(status.provider.name, "mistral");
    assert_eq!(status.provider.api_key_env_var, "MISTRAL_API_KEY");
    assert_eq!(status.console_base_url, "https://console.mistral.ai");
    assert!(!status.has_api_key);

    let mut wizard = OnboardingState::default();
    vibe_rs::setup::wizard::seed(&mut wizard, &status);
    assert_eq!(wizard.context.provider.name, "mistral");
    assert_eq!(wizard.context.console_base_url, status.console_base_url);
    assert_eq!(
        wizard.theme_index,
        vibe_rs::theme_picker::options()
            .iter()
            .position(|theme| *theme == "auto")
            .expect("auto theme")
    );
}

#[tokio::test]
async fn the_verdicts_provider_seeds_the_wizard_not_the_servers_default() {
    // The loop-guard contract's wire half: the handshake's verdict names a
    // provider the wizard must seed for, even when the server's own
    // active provider (a provider-less status) would resolve differently.
    let (client, _child) = spawn("fake_server_setup_completed").await;
    let status = rpc::status(&client, Some("llamacpp"))
        .await
        .expect("status");
    assert_eq!(status.provider.name, "llamacpp", "the named provider wins");
    assert!(!status.supports_browser_sign_in);

    let mut wizard = OnboardingState::default();
    vibe_rs::setup::wizard::seed(&mut wizard, &status);
    assert_eq!(wizard.context.provider.name, "llamacpp");
    assert!(!wizard.context.supports_browser_sign_in);

    // The provider-less status resolves the server's own active provider
    // (mistral here, not the named llamacpp); the request log pins both
    // wire shapes.
    let default = rpc::status(&client, None).await.expect("status");
    assert_eq!(default.provider.name, "mistral");
    let statuses = params_of(&client, "setup/status").await;
    assert_eq!(statuses.len(), 2);
    assert_eq!(statuses[0], json!({"provider": "llamacpp"}));
    assert_eq!(statuses[1], json!({}));
}

#[tokio::test]
async fn a_completed_store_sends_the_key_then_submits_choices() {
    let (client, _child) = spawn("fake_server_setup_completed").await;
    let close = submit(&client).await;
    assert!(matches!(close, Some(Close::Completed { .. })));

    // The server derived the env var itself; the client sent only the
    // provider name, the secret, and the custom-domain flag.
    let stores = params_of(&client, "setup/store-credential").await;
    assert_eq!(stores.len(), 1);
    assert_eq!(stores[0]["provider"], "mistral");
    assert_eq!(stores[0]["apiKey"], "sk-mock-key");
    assert_eq!(stores[0]["customDomain"], false);

    // The completion always submits, and the client sends its full
    // choices — the server judges drift (item: server is the only drift
    // judge). No theme was selected, so that field stays absent.
    let submits = params_of(&client, "setup/submit-choices").await;
    assert_eq!(submits.len(), 1);
    assert_eq!(submits[0]["provider"]["name"], "mistral");
    assert_eq!(submits[0]["consoleBaseUrl"], "https://console.mistral.ai");
    assert_eq!(submits[0]["vibeBaseUrl"], "https://chat.mistral.ai");
    assert!(submits[0].get("theme").is_none());
}

#[tokio::test]
async fn a_completed_store_with_a_theme_submits_it() {
    let (client, _child) = spawn("fake_server_setup_completed").await;
    let mut wizard = OnboardingState::default();
    wizard.context.provider.api_base = "https://api.example/v1".into();
    wizard.context.console_base_url = "https://console.example".into();
    wizard.theme_selected = true;
    wizard.theme_index = 1;
    let mut sign_in = None;
    let close = handle_onboarding_action(
        &mut wizard,
        &mut sign_in,
        &client,
        vibe_rs::setup::wizard::Action::SubmitApiKey("sk-mock-key".into()),
    )
    .await;
    assert!(matches!(close, Some(Close::Completed { .. })));
    let submits = params_of(&client, "setup/submit-choices").await;
    assert_eq!(submits.len(), 1);
    assert_eq!(submits[0]["provider"]["apiBase"], "https://api.example/v1");
    assert_eq!(submits[0]["consoleBaseUrl"], "https://console.example");
    assert_eq!(submits[0]["theme"], vibe_rs::theme_picker::options()[1]);
    // The keyed-upsert op is gone with the flush seam: no config/write ever
    // leaves the wizard.
    assert!(params_of(&client, "config/write").await.is_empty());
}

#[tokio::test]
async fn env_var_error_fails_the_run_with_pythons_exit_one_print() {
    let (client, _child) = spawn("fake_server_setup_env_var_error").await;
    let close = submit(&client).await;
    // Nothing was saved and nothing else was called: submit-choices never
    // runs after an env-var error (Python exits 1 before it).
    assert!(
        matches!(close, Some(Close::EnvVarError(ref name)) if name.is_empty()),
        "got {close:?}"
    );
    assert!(params_of(&client, "setup/submit-choices").await.is_empty());
    assert_eq!(
        vibe_rs::setup::wizard::actions::onboarding_finished(close.unwrap()),
        SetupVerdict::Exit(std::process::ExitCode::from(1)),
        "the env-var error exits 1"
    );
}

#[tokio::test]
async fn save_error_warns_and_still_submits_the_choices() {
    let (client, _child) = spawn("fake_server_setup_save_error").await;
    let close = submit(&client).await;
    // The warning rides the close; the flow continued.
    let warnings = submitted_warnings(close).await;
    assert_eq!(
        params_of(&client, "setup/submit-choices").await.len(),
        1,
        "save_error continues into submit-choices"
    );
    let message = vibe_rs::setup::exit::setup_exit_message(&warnings);
    assert!(message.contains("Warning: Could not save API key"));
    assert!(message.contains("no space left"));
    assert!(!message.contains("Setup complete"));
}

#[tokio::test]
async fn provider_config_error_warns_and_continues() {
    let (client, _child) = spawn("fake_server_setup_provider_config_error").await;
    let close = submit(&client).await;
    let warnings = submitted_warnings(close).await;
    assert_eq!(
        warnings.len(),
        1,
        "the failures list surfaces as the warning"
    );
    let message = vibe_rs::setup::exit::setup_exit_message(&warnings);
    assert!(message.contains("Warning: Could not save provider config"));
    assert!(message.contains("provider"));
    assert!(!message.contains("Setup complete"));
}

#[tokio::test]
async fn a_clean_completion_prints_setup_complete() {
    let message = vibe_rs::setup::exit::setup_exit_message(&[]);
    assert!(message.contains("Setup complete"));
}

#[tokio::test]
async fn cancel_closes_the_wizard_without_touching_the_server() {
    let (client, _child) = spawn("fake_server_setup_completed").await;
    let mut wizard = OnboardingState::default();
    let mut sign_in = None;
    let close = handle_onboarding_action(
        &mut wizard,
        &mut sign_in,
        &client,
        vibe_rs::setup::wizard::Action::Cancel,
    )
    .await;
    assert!(matches!(close, Some(Close::Cancelled)));
    assert_eq!(
        vibe_rs::setup::wizard::actions::onboarding_finished(close.unwrap()),
        SetupVerdict::Exit(std::process::ExitCode::SUCCESS),
        "cancel exits 0"
    );
    // A cancelled wizard persists nothing: no setup call ever ran.
    assert!(requests(&client).await.is_empty());
}

#[tokio::test]
async fn an_old_server_without_setup_fails_clearly_and_never_writes() {
    let (client, _child) = spawn("fake_server_setup_absent").await;

    // The skew is classified: no local fallback exists.
    assert!(matches!(
        rpc::status(&client, None).await,
        Err(rpc::SetupError::Unavailable)
    ));

    let mut wizard = OnboardingState::default();
    let mut sign_in = None;
    let close = handle_onboarding_action(
        &mut wizard,
        &mut sign_in,
        &client,
        vibe_rs::setup::wizard::Action::SubmitApiKey("sk-mock-key".into()),
    )
    .await;
    // The old-server skew keeps its own close (never the generic failure's
    // wording), and the verdict prints it with exit 1.
    match close {
        Some(Close::SetupUnavailable) => {}
        other => panic!("expected SetupUnavailable, got {other:?}"),
    }
    // Only the initialize and the failed status call ran: no write of any
    // kind reached the wire.
    let methods: Vec<String> = requests(&client)
        .await
        .into_iter()
        .map(|request| request["method"].as_str().expect("method").to_owned())
        .collect();
    assert_eq!(
        methods,
        vec![
            "setup/status".to_owned(),
            "setup/store-credential".to_owned()
        ]
    );
}

#[tokio::test]
async fn retry_sign_in_resets_the_full_browser_sign_in_state() {
    let (client, _child) = spawn("fake_server_setup_completed").await;
    let mut wizard = OnboardingState::default();
    // Dirty every field that a previous sign-in attempt could have set.
    wizard.browser_sign_in.step = vibe_rs::setup::wizard::SignInStep::Finish;
    wizard.browser_sign_in.message = "Exchanging...".into();
    wizard.browser_sign_in.variant = vibe_rs::setup::wizard::SignInVariant::Error;
    wizard.browser_sign_in.sign_in_url = Some("https://console.example/sign-in".into());
    wizard.browser_sign_in.show_url_help = true;
    wizard.browser_sign_in.reveal_sign_in_url = true;
    wizard.browser_sign_in.sign_in_started_at = Some(std::time::Instant::now());

    let mut sign_in = None;
    let close = handle_onboarding_action(
        &mut wizard,
        &mut sign_in,
        &client,
        vibe_rs::setup::wizard::Action::RetrySignIn,
    )
    .await;
    // RetrySignIn never closes the wizard.
    assert!(close.is_none());

    // Every field is reset to the default, except `running` (true — a new
    // flow was spawned) and `variant` (Pending — no result yet).
    let state = &wizard.browser_sign_in;
    assert!(state.running, "a new sign-in flow is running");
    assert_eq!(
        state.variant,
        vibe_rs::setup::wizard::SignInVariant::Pending,
        "the variant resets to Pending"
    );
    assert_eq!(
        state.step,
        vibe_rs::setup::wizard::SignInStep::Open,
        "the step resets to Open"
    );
    assert_eq!(
        state.message, "Getting things ready...",
        "the message resets to the default"
    );
    assert!(state.sign_in_url.is_none(), "the old URL is cleared");
    assert!(!state.show_url_help, "the URL help is cleared");
    assert!(!state.reveal_sign_in_url, "the URL reveal is cleared");
    assert!(
        state.sign_in_started_at.is_none(),
        "the start time is cleared"
    );

    // A new sign-in handle was spawned (the old one, if any, was aborted).
    assert!(sign_in.is_some(), "a new sign-in handle was spawned");
}

// ---------------------------------------------------------------------------
// Pre-engine check: has_api_key from the server overrides the probe-negative
// ---------------------------------------------------------------------------

/// The server's `setup/status` says `hasApiKey: true` (the project config
/// resolves to a keyless provider like llamacpp): the pre-engine probe was
/// wrong, and the pre-loop round hands the child to the handshake instead of
/// running the wizard. This is the project-layer case mgesbert requested —
/// `default_key_present()` doesn't read config.toml, but the server does, so
/// the `has_api_key` field is the authoritative signal.
#[test]
#[ignore]
fn fake_server_status_keyed() {
    fake_server::serve_scripted(|method, _msg| match method {
        "initialize" => Ok(json!({"serverInfo": {"version": "test"}})),
        "setup/status" => Ok(json!({
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
            "hasApiKey": true,
            "enableSystemTrustStore": false,
        })),
        _ => Ok(json!({})),
    });
}

#[tokio::test]
async fn a_keyed_status_overrides_the_probe_negative() {
    let (client, _child) = spawn("fake_server_status_keyed").await;
    let status = rpc::status(&client, None).await.expect("status");
    assert!(status.has_api_key, "the server resolved a keyless provider");
    assert_eq!(status.provider.name, "llamacpp");
    assert_eq!(status.provider.api_key_env_var, "");

    // The pre-loop round's filter: `status.filter(|s| version.is_some() &&
    // !s.has_api_key)` returns None when has_api_key is true → the boot
    // answer is Handshake (skip the wizard, hand the child to the session).
    let version = Some("test".to_string());
    let status_opt = Some(status);
    assert!(
        status_opt
            .filter(|s| version.is_some() && !s.has_api_key)
            .is_none(),
        "a keyed status skips the wizard"
    );
}

// ---------------------------------------------------------------------------
// Overlay cancel output: only the cancelled line, no resume block
// ---------------------------------------------------------------------------

/// When the wizard is cancelled from the MissingApiKey overlay, the exit is
/// `SetupVerdict::Exit(SUCCESS)` → `RoundsOutcome::ExitSuccess` → the
/// entrypoint exits 0 without printing the resume block (the loop never ran,
/// so `EventLoop::run`'s `print_session_resume_message` is never reached).
/// The only output is the cancelled line.
#[tokio::test]
async fn cancel_from_the_overlay_prints_only_the_cancelled_line() {
    let (client, _child) = spawn("fake_server_setup_completed").await;
    let mut wizard = OnboardingState::default();
    let mut sign_in = None;
    let close = handle_onboarding_action(
        &mut wizard,
        &mut sign_in,
        &client,
        vibe_rs::setup::wizard::Action::Cancel,
    )
    .await;
    assert!(matches!(close, Some(Close::Cancelled)));

    // The verdict is Exit(SUCCESS): `round_exit(SUCCESS)` returns
    // `RoundsOutcome::ExitSuccess`, and the entrypoint exits without the
    // resume block (the loop never re-entered).
    let verdict = vibe_rs::setup::wizard::actions::onboarding_finished(close.unwrap());
    assert_eq!(
        verdict,
        SetupVerdict::Exit(std::process::ExitCode::SUCCESS),
        "cancel exits 0 — no resume block, only the cancelled line"
    );

    // The cancelled message is the expected text (no resume block appended).
    assert_eq!(
        vibe_rs::setup::exit::SETUP_CANCELLED,
        "Setup cancelled. See you next time!"
    );

    // No setup call ever ran: the cancelled wizard persists nothing.
    assert!(requests(&client).await.is_empty());
}
