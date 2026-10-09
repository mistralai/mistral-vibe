//! The `--setup` exit surface: Python's post-wizard messages and the
//! session-less exit that reaps the child. No session is ever opened, so
//! the trust gate never fires and nothing lands through `config/write`.

use std::io::IsTerminal;
use std::sync::Arc;

use anyhow::Result;

use crate::server::{child::ChildHandle, Client};
use crate::startup::StartupRecorder;
use crate::ui::theme::fixed::sgr;
use crate::utils::history_persist::Persister;

pub const SETUP_CANCELLED: &str = "Setup cancelled. See you next time!";
pub const SETUP_COMPLETE: &str =
    "Setup complete 🎉. Run \"vibe\" to start using the Mistral Vibe CLI.";

/// The completed-setup exit line: Python's `rich.print` highlights the quoted
/// `\"vibe\"` in green and keeps the rest in the terminal default, dropping the
/// color off-TTY and under NO_COLOR exactly like rich does.
pub fn setup_complete_message() -> String {
    let green =
        crate::session_exit::color_span(std::io::stdout().is_terminal(), sgr::GREEN, sgr::GREEN)
            .filter(|_| !crate::session_exit::no_color());
    match green {
        Some(code) => format!(
            "\n{}\n",
            SETUP_COMPLETE.replace("\"vibe\"", &format!("{code}\"vibe\"\u{1b}[0m"))
        ),
        None => format!("\n{SETUP_COMPLETE}\n"),
    }
}

/// Python `save_error`'s warning: the key lives in the server process env
/// for this run only (the keyring and `.env` writes both failed).
pub fn save_warning_message(error: &str) -> String {
    let path = crate::utils::paths::vibe_home()
        .map(|home| home.join(".env").display().to_string())
        .unwrap_or_else(|| "the .env file under your Vibe home".to_owned());
    format!(
        "\nWarning: Could not save API key to .env file: {error}\n\
         The API key is set for this session only. \
         You may need to set it manually in {path}\n"
    )
}

/// Python `provider_config_error`'s exit message: the key was saved, but the
/// provider config write failed.
pub fn provider_config_warning(error: &str) -> String {
    format!(
        "\nWarning: Could not save provider config: {error}\n\
         The API key was saved, but the custom domain provider configuration \
         was not persisted to config.toml. You may need to update your \
         provider's browser_auth_base_url / browser_auth_api_base_url \
         settings manually.\n"
    )
}

/// Python `run_onboarding`'s `env_var_error` exit print: nothing was saved
/// and the run exits 1. The failure line is yellow, the remedy dim.
pub fn print_env_var_error(env_key: &str) {
    let enabled = std::io::stdout().is_terminal() && !crate::session_exit::no_color();
    let (yellow, dim) = if enabled {
        (sgr::YELLOW, "\x1b[2m")
    } else {
        ("", "")
    };
    let reset = if enabled { "\x1b[0m" } else { "" };
    print_setup_exit(format!(
        "\n{yellow}Could not save the API key because this provider is \
         configured with an invalid environment variable name: {env_key}.{reset}\n\
         {dim}The API key was not saved for this session. Update the \
         provider's api_key_env_var setting in your config and try again.{reset}\n"
    ));
}

/// The interactive paths' post-wizard warnings (Python prints both in
/// every mode): the same warning lines as `--setup`, without the
/// completion line — a clean wizard run stays silent.
pub fn warnings_message(warnings: &[String]) -> Option<String> {
    (!warnings.is_empty()).then(|| warnings.concat())
}

/// Print the wizard's continue-anyway warnings on the real terminal before
/// the TUI takes over, like Python's pre-TUI rprint: suspend the alternate
/// screen, print, re-enter, and clear so the next frame paints in full.
pub fn print_pre_tui_warning(
    message: &str,
    terminal: &mut crate::terminal::Tui,
    guard: &mut crate::terminal::TerminalGuard,
) -> Result<()> {
    guard.suspend();
    {
        use std::io::Write;
        let mut out = std::io::stdout();
        let _ = writeln!(out, "{message}");
        let _ = out.flush();
    }
    guard.resume()?;
    terminal.clear()?;
    Ok(())
}

/// The old-server skew: `setup/*` is absent, and no local fallback writer
/// exists (decision 10) — the run fails with a clear error instead.
pub fn print_setup_unavailable(detail: &str) {
    print_setup_exit(format!(
        "\nSetup is unavailable: {detail}.\n\
         Update the Mistral Vibe CLI and app-server, then run setup again.\n"
    ));
}

/// The wizard could not run at all: the app-server never started, or a
/// `setup/*` call failed on the wire — a plain failure, never the
/// old-server skew's version wording.
pub fn print_setup_failed(detail: &str) {
    print_setup_exit(format!("\nSetup failed: {detail}.\n"));
}

/// Print Python's post-wizard message after the alternate screen closes;
/// the terminal guard's later restore is idempotent.
pub fn print_setup_exit(message: String) {
    ratatui::restore();
    println!("{message}");
}

/// Print the cancelled message in Python's `[yellow]`: rich keeps named ANSI
/// colors at every depth, and drops them under NO_COLOR or off-TTY.
pub fn print_setup_cancelled() {
    let yellow =
        crate::session_exit::color_span(std::io::stdout().is_terminal(), sgr::YELLOW, sgr::YELLOW)
            .filter(|_| !crate::session_exit::no_color());
    let message = match yellow {
        Some(code) => format!("\n{code}{SETUP_CANCELLED}\x1b[0m"),
        None => format!("\n{SETUP_CANCELLED}"),
    };
    print_setup_exit(message);
}

/// The `--setup` exit line: Python's completion message, or the
/// save/provider warnings — the two continue-anyway outcomes replace the
/// success line exactly like Python's exclusive match.
pub fn setup_exit_message(warnings: &[String]) -> String {
    warnings_message(warnings).unwrap_or_else(setup_complete_message)
}

/// `--setup`'s session-less end: the wizard already ran `setup/*` on the
/// initialized connection, so the outcome prints and the child is reaped —
/// no handshake, no session, no trust gate.
pub async fn finish_setup(
    client: &Arc<Client>,
    child: ChildHandle,
    warnings: Vec<String>,
) -> Result<std::process::ExitCode> {
    print_setup_exit(setup_exit_message(&warnings));
    // stdin EOF is the server's exit signal; the child is reaped after.
    client.close_stdin().await;
    match child.wait_with_grace().await {
        Some(status) => tracing::debug!("app-server reaped after setup: {status}"),
        None => tracing::debug!("app-server SIGKILLed after setup grace"),
    }
    Ok(std::process::ExitCode::SUCCESS)
}

/// The exit flushes every `--setup` exit path runs, in the normal loop's
/// order: history, timings, then Sentry.
pub fn exit_flushes(history_flush: &Arc<Persister>, timings: &StartupRecorder) {
    history_flush.flush();
    timings.flush();
    crate::observability::sentry::flush();
}
