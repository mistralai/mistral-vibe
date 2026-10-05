//! The wizard's action handling and completion (Python `run_onboarding`'s
//! exit match). Every write goes through the `setup/*` RPCs on the
//! initialized connection — nothing persists client-side (ADR 0016).

use std::sync::Arc;

use crate::server::Client;
use crate::setup::auth::browser_sign_in::{self, SignInHandle};
use crate::setup::auth::rpc;
use crate::setup::wizard::{BrowserSignInState, SignInVariant};

use super::boot::SetupBoot;
use super::context;
use super::submit;
use super::{Action, OnboardingState};

/// How the wizard closed, mirroring the result cases of Python's `run_onboarding`.
#[derive(Debug)]
pub enum Close {
    Cancelled,
    /// The wizard's choices landed; the two continue-anyway warnings ride
    /// along (Python prints them in every mode).
    Completed {
        warnings: Vec<String>,
    },
    /// `store-credential` answered `env_var_error`: nothing was saved, and
    /// the run exits 1 (Python's `env_var_error` match).
    EnvVarError(String),
    /// The `setup/*` surface is absent (an old server): the wizard never
    /// falls back to local writes.
    SetupUnavailable,
    /// A `setup/*` call failed on the wire.
    SetupFailed(String),
    /// `--setup`'s background boot failed behind the painted welcome
    /// screen (the wizard already closed): the message prints here.
    BootFailed(SetupBoot),
}

/// Handle one wizard action. Async because the key submit talks to the
/// app-server (`setup/store-credential`) and a non-default console first
/// resolves tenant domains over HTTP (Python awaits `resolve_tenant_domains`
/// inside `persist_credentials`); the wizard flow pauses redraws for at most
/// the whoami timeout.
pub async fn handle_onboarding_action(
    wizard: &mut OnboardingState,
    sign_in_rx: &mut Option<SignInHandle>,
    client: &Arc<Client>,
    action: Action,
) -> Option<Close> {
    match action {
        Action::Cancel => {
            // Python's browser sign-in `action_cancel` cancels the attempt
            // before the wizard closes.
            if let Some(previous) = sign_in_rx.take() {
                previous.abort();
            }
            super::close(wizard);
            Some(Close::Cancelled)
        }
        Action::SelectTheme(idx) => {
            let options = crate::theme_picker::options();
            if let Some(name) = options.get(idx) {
                // Live preview only; the theme rides `setup/submit-choices`
                // at completion (Python persists it once the wizard ends).
                crate::ui::theme::set_active(name);
                wizard.theme_selected = true;
            }
            None
        }
        Action::StartBrowserSignIn | Action::RetrySignIn => {
            // Abort the previous flow first: it keeps polling its old
            // process and could still open a browser tab.
            if let Some(previous) = sign_in_rx.take() {
                previous.abort();
            }
            *sign_in_rx = Some(browser_sign_in::spawn(
                wizard
                    .context
                    .provider
                    .browser_auth_base_url
                    .as_deref()
                    .unwrap_or_default(),
                wizard
                    .context
                    .provider
                    .browser_auth_api_base_url
                    .as_deref()
                    .unwrap_or_default(),
                wizard.context.provider.browser_auth_allow_origin_rewrite,
                wizard.context.enable_system_trust_store,
            ));
            wizard.browser_sign_in = BrowserSignInState {
                running: true,
                variant: SignInVariant::Pending,
                ..Default::default()
            };
            tracing::info!("browser sign-in started from onboarding");
            None
        }
        Action::AbortSignIn => {
            // Python `action_manual`: cancel the attempt before the API key
            // screen, or a browser completed in the background would submit
            // its key and close the wizard.
            if let Some(previous) = sign_in_rx.take() {
                previous.abort();
            }
            wizard.browser_sign_in.running = false;
            None
        }
        Action::CopySignInUrl => {
            if let Some(url) = wizard.browser_sign_in.sign_in_url.clone() {
                crate::clipboard::copy_to_clipboard(&url);
                wizard.browser_sign_in.show_url_help = true;
                wizard.browser_sign_in.reveal_sign_in_url = true;
            }
            None
        }
        Action::SubmitApiKey(key) => submit_api_key(wizard, client, &key).await,
    }
}

/// The key submit: `setup/store-credential` (the server derives the env
/// var and owns the keyring/`.env` writes), then — unless nothing could be
/// saved — `setup/submit-choices` for the provider/URL/theme half.
async fn submit_api_key(
    wizard: &mut OnboardingState,
    client: &Arc<Client>,
    api_key: &str,
) -> Option<Close> {
    // Python `persist_api_key`'s telemetry `custom_domain` property: the
    // wizard configured a non-default browser auth base.
    let custom_domain = context::configured_custom_domain(&wizard.context.provider).is_some();
    let outcome = match rpc::store_credential(
        client,
        &wizard.context.provider.name,
        api_key,
        custom_domain,
    )
    .await
    {
        Ok(outcome) => outcome,
        Err(rpc::SetupError::Unavailable) => return Some(Close::SetupUnavailable),
        Err(rpc::SetupError::Failed(error)) => {
            tracing::error!(%error, "setup/store-credential failed");
            return Some(Close::SetupFailed(error));
        }
    };
    let mut warnings = Vec::new();
    match outcome {
        rpc::StoreOutcome::EnvVarError { detail: name } => return Some(Close::EnvVarError(name)),
        rpc::StoreOutcome::SaveError { detail: error } => {
            // Python `save_error`: warn and continue — the key lives in the
            // server process env for this run, so the session still
            // authenticates and the choices still submit.
            tracing::warn!(%error, "could not save API key");
            warnings.push(crate::setup::exit::save_warning_message(&error));
        }
        rpc::StoreOutcome::Completed => {}
    }
    submit_choices(wizard, client, api_key, &mut warnings).await
}

/// Python `persist_credentials`' config half: a non-default console resolves
/// the tenant's own API and chat hosts from /whoami first (client-side: it
/// needs only the API key), then the wizard's choices land through
/// `setup/submit-choices` — the full choices every time, drift judged
/// server-side, the theme whenever one was selected.
async fn submit_choices(
    wizard: &mut OnboardingState,
    client: &Arc<Client>,
    api_key: &str,
    warnings: &mut Vec<String>,
) -> Option<Close> {
    if wizard.context.console_base_url != context::DEFAULT_CONSOLE_BASE_URL
        // Python `persist_credentials` returns before `resolve_tenant_domains`
        // when provider, console, and vibe URL still equal the config
        // snapshot: an unchanged on-prem run pays no extra authenticated
        // request and keeps its configured bases.
        && !context::matches_launch_snapshot(&wizard.context, &wizard.config)
    {
        let (provider, vibe) = crate::setup::auth::whoami::resolve_tenant_domains(
            &wizard.context.provider,
            &wizard.context.console_base_url,
            api_key,
            &wizard.context.vibe_base_url,
            wizard.context.enable_system_trust_store,
        )
        .await;
        wizard.context.provider = provider;
        wizard.context.vibe_base_url = vibe;
    }
    let choices = submit::choices(wizard);
    let outcome = match rpc::submit_choices(client, &choices).await {
        Ok(outcome) => outcome,
        Err(rpc::SetupError::Unavailable) => return Some(Close::SetupUnavailable),
        Err(rpc::SetupError::Failed(error)) => {
            tracing::error!(%error, "setup/submit-choices failed");
            return Some(Close::SetupFailed(error));
        }
    };
    if let rpc::SubmitOutcome::ProviderConfigError { failures } = outcome {
        // Python `provider_config_error`: the key was saved, but the
        // provider config write failed — surfaced, never swallowed.
        let error = failures.join("; ");
        tracing::warn!(%error, "could not persist provider config");
        warnings.push(crate::setup::exit::provider_config_warning(&error));
    }
    super::close(wizard);
    Some(Close::Completed {
        warnings: std::mem::take(warnings),
    })
}

/// Python `run_onboarding`'s exit match, folded into the round's verdict:
/// every terminal outcome prints inside — cancel exits 0, the failures
/// exit 1, completion continues with its warnings riding along.
pub fn onboarding_finished(close: Close) -> crate::setup::SetupVerdict {
    match close {
        Close::Cancelled => {
            crate::setup::exit::print_setup_cancelled();
            crate::setup::SetupVerdict::Exit(std::process::ExitCode::SUCCESS)
        }
        Close::Completed { warnings } => crate::setup::SetupVerdict::Continue { warnings },
        Close::EnvVarError(name) => {
            crate::setup::exit::print_env_var_error(&name);
            crate::setup::SetupVerdict::Exit(std::process::ExitCode::from(1))
        }
        Close::SetupUnavailable => {
            crate::setup::exit::print_setup_unavailable(
                "this app-server does not support the setup methods",
            );
            crate::setup::SetupVerdict::Exit(std::process::ExitCode::from(1))
        }
        Close::SetupFailed(detail) => {
            crate::setup::exit::print_setup_failed(&detail);
            crate::setup::SetupVerdict::Exit(std::process::ExitCode::from(1))
        }
        Close::BootFailed(failure) => {
            failure.print_failure();
            crate::setup::SetupVerdict::Exit(std::process::ExitCode::from(1))
        }
    }
}
