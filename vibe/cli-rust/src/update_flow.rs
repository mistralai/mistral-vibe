//! The startup update flows (Python `cli.py` `run_cli`'s update paths).

use crate::observability;
use crate::ui;
use crate::update_notifier;
use crate::update_prompt;
use crate::utils;

/// Python `_run_check_upgrade` (cli.py): forced PyPI check, then the outcome
/// line or the update dialog in `CheckUpgrade` mode. Python reads the theme
/// from live config; the cached startup config is this client's only source
/// before the app-server is up.
pub async fn run_check_upgrade() -> std::process::ExitCode {
    use update_notifier::cache::FileSystemUpdateCacheRepository;
    use update_notifier::gateway::{UpdateCheckGateway, UpdateGateway};
    use update_notifier::update::get_update_if_available;
    use update_prompt::color_span;
    use update_prompt::{UpdatePromptMode, UpdatePromptState};

    let startup_config = prepare_theme_from_startup_cache();
    // ADR 0015: apply the trust policy before the gateway builds its client;
    // a stale or missing cache defaults to the bundled roots.
    update_notifier::gateway::configure_tls_trust(startup_config.enable_system_trust_store);
    let current = env!("CARGO_PKG_VERSION");
    let now = utils::now_unix();
    let repository = FileSystemUpdateCacheRepository;
    let gateway = UpdateCheckGateway::for_project("mistral-vibe").await;
    let update = match get_update_if_available(&gateway, current, &repository, now, true).await {
        Ok(update) => update,
        Err(error) => {
            // Python `_run_check_upgrade` prints the failed check and exits 1.
            // A cache that cannot be written never lands here: the write's
            // failure is logged inside, like Python's swallowed `OSError`.
            println!(
                "{} {}",
                color_span(
                    ui::theme::text(ui::theme::error()),
                    "✗ Update check failed:"
                ),
                error.message
            );
            return std::process::ExitCode::from(1);
        }
    };
    let Some(availability) = update else {
        // A git-based install has no registry latest: uv never lists it as
        // outdated, so "already up to date" would be a claim we cannot make.
        // `uv tool upgrade` still re-resolves the branch and upgrades it,
        // so point at that instead.
        if update_notifier::uv_oracle::git_source().await.is_some() {
            println!(
                "{}",
                color_span(
                    ui::theme::text(ui::theme::secondary()),
                    "Vibe was installed from git; uv cannot check for updates to it."
                )
            );
            println!(
                "  Update by running: {}",
                color_span(
                    ratatui::style::Style::default().add_modifier(ratatui::style::Modifier::BOLD),
                    "uv tool upgrade mistral-vibe"
                )
            );
            return std::process::ExitCode::SUCCESS;
        }
        println!(
            "{}",
            color_span(
                ui::theme::text(ui::theme::success()),
                &format!("Vibe is already up to date ({current}).")
            )
        );
        return std::process::ExitCode::SUCCESS;
    };
    // A pinned uv install cannot act on the dialog's offer: `uv tool
    // upgrade` re-resolves within the recorded requirement, so the update
    // would be the "Nothing to upgrade" no-op. Answer the explicit ask with
    // the pin and the way out instead of the dialog.
    if let Some(pin) = update_notifier::uv_pin::blocking_pin(&availability.latest_version).await {
        println!(
            "{}",
            color_span(
                ui::theme::text(ui::theme::secondary()),
                &format!(
                    "Vibe {} is available, but your uv install is pinned to {}.",
                    availability.latest_version, pin
                )
            )
        );
        println!(
            "  Update by running: {}",
            color_span(
                ratatui::style::Style::default().add_modifier(ratatui::style::Modifier::BOLD),
                "uv tool install mistral-vibe"
            )
        );
        return std::process::ExitCode::SUCCESS;
    }
    let state = UpdatePromptState::new(
        current,
        &availability.latest_version,
        UpdatePromptMode::CheckUpgrade,
        gateway.source(),
    );
    match update_prompt::show_update_prompt(&state, false).await {
        Some(code) => std::process::ExitCode::from(u8::try_from(code).unwrap_or(1)),
        None => std::process::ExitCode::SUCCESS,
    }
}

/// Activate the theme the dialog renders with: the cached startup config's,
/// or `auto` before a first run has cached anything. Returns the loaded
/// config so the caller can seed the trust policy from it (ADR 0015).
fn prepare_theme_from_startup_cache() -> utils::startup_cache::StartupConfig {
    let config = utils::startup_cache::StartupConfig::load().unwrap_or_default();
    ui::theme::prepare_active(&config.theme, crate::theme_detection::resolve_auto_theme());
    config
}

/// Python `_maybe_run_startup_update_prompt` (cli.py): show the Startup-mode
/// dialog for a cached pending update, dismissing it when the user continues.
/// Divergence: Python gates on live config; this client has no local config
/// read before the app-server is up, so the gate is the previous run's cached
/// `enable_update_checks` (default true on a first run). On a worktree run
/// the app-server is spawned already; a dialog that exits must kill its
/// process group here, because `process::exit` skips `ChildHandle`'s Drop.
pub async fn maybe_run_startup_update_prompt(
    startup_config: &utils::startup_cache::StartupConfig,
    child: Option<&crate::server::ChildHandle>,
) {
    if !startup_config.enable_update_checks {
        return;
    }
    let repository = update_notifier::cache::FileSystemUpdateCacheRepository;
    // The manager probes only run once a pending update exists at all: a
    // quiet cache must not cost a `uv tool dir` / `brew --cellar` subprocess.
    let Some(pending) =
        update_notifier::update::pending_update_from_cache(&repository, env!("CARGO_PKG_VERSION"))
    else {
        return;
    };
    // A pending answer is only shown when the reader's own install manager
    // wrote it: another install's check may advertise a version this
    // install's manager cannot deliver.
    let source = update_notifier::gateway::install_source().await;
    if !update_notifier::update::entry_source_trusted(pending.source.as_deref(), source) {
        return;
    }
    // A pinned install cannot act on the banner's offer, so the prompt stays
    // quiet; the pin is re-read from the receipt, never trusted from a cache.
    if update_notifier::uv_pin::blocking_pin(&pending.latest_version)
        .await
        .is_some()
    {
        return;
    }
    let latest = pending.latest_version;
    let state = update_prompt::UpdatePromptState::new(
        env!("CARGO_PKG_VERSION"),
        &latest,
        update_prompt::UpdatePromptMode::Startup,
        source,
    );
    if let Some(code) = update_prompt::show_update_prompt(&state, true).await {
        if let Some(child) = child {
            child.kill_now();
        }
        // process::exit bypasses run()'s flush, and the dialog is the last
        // surface that could have captured anything.
        observability::sentry::flush();
        std::process::exit(code);
    }
}
