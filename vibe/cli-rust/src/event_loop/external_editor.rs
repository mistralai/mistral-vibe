//! Ctrl+G: hand the terminal to the external editor, then load what it saved.

use std::ffi::OsString;
use std::sync::Arc;

use anyhow::Result;

use crate::app::{App, ToastSeverity};
use crate::external_editor;
use crate::server::signal::ShutdownSignal;
use crate::server::{Client, Notification};

use super::handoff::TerminalHandoff;
use super::notifications::absorb_notification;
use super::EventSources;

const FAILURE_TOAST_SECS: u64 = 6;
const EDITOR_EXIT_GRACE: std::time::Duration = std::time::Duration::from_millis(500);

enum Outcome {
    Saved,
    Cancelled,
    Failed(std::io::Error),
}

/// Edit the composer text in the external editor; true when a shutdown signal ended the run.
pub(super) async fn open_external_editor(
    app: &mut App,
    client: &Arc<Client>,
    handoff: TerminalHandoff<'_>,
    sources: &mut EventSources,
    shutdown: &mut ShutdownSignal,
    deferred: &mut Option<Notification>,
) -> Result<bool> {
    let initial = app.chat_input.submitted_text();
    let editor =
        external_editor::get_editor(std::env::var("VISUAL").ok(), std::env::var("EDITOR").ok());
    let file = match draft_file(&initial) {
        Ok(file) => file,
        Err(error) => {
            show_error(app, format!("Could not create the file to edit: {error}"));
            return Ok(false);
        }
    };
    let argv = external_editor::command(&editor, &file);
    let lent = handoff.begin().await;
    let Some(outcome) = wait_for_editor(&argv, app, client, sources, shutdown, deferred).await
    else {
        lent.abandon();
        return Ok(true);
    };
    // The saved text lands before the terminal is taken back, so a terminal error cannot drop it.
    match outcome {
        Outcome::Saved => match std::fs::read(&file) {
            Ok(bytes) => {
                let saved = String::from_utf8_lossy(&bytes);
                if let Some(text) = external_editor::edited_text(&initial, &saved) {
                    external_editor::apply(app, text);
                }
            }
            Err(error) => show_error(app, format!("Could not read the edited text: {error}")),
        },
        Outcome::Failed(error) => {
            show_error(app, format!("Could not open editor {editor}: {error}"));
        }
        Outcome::Cancelled => {}
    }
    lent.end()?;
    app.terminal_notifier.invalidate_title();
    Ok(false)
}

/// A closed `vibe_*.md` temp file holding the draft, removed on drop.
fn draft_file(initial: &str) -> std::io::Result<tempfile::TempPath> {
    let mut file = tempfile::Builder::new()
        .prefix("vibe_")
        .suffix(".md")
        .tempfile()?;
    std::io::Write::write_all(&mut file, initial.as_bytes())?;
    Ok(file.into_temp_path())
}

/// Run the editor to completion while the agent's notifications keep flowing; `None` on shutdown.
async fn wait_for_editor(
    argv: &[OsString],
    app: &mut App,
    client: &Arc<Client>,
    sources: &mut EventSources,
    shutdown: &mut ShutdownSignal,
    deferred: &mut Option<Notification>,
) -> Option<Outcome> {
    let Some((program, args)) = argv.split_first() else {
        return Some(Outcome::Cancelled);
    };
    let spawned = tokio::process::Command::new(program)
        .args(args)
        .kill_on_drop(true)
        .spawn();
    let mut child = match spawned {
        Ok(child) => child,
        Err(error) => return Some(Outcome::Failed(error)),
    };
    loop {
        tokio::select! {
            biased;
            _ = shutdown.wait_ignoring_interrupt() => {
                stop(&mut child).await;
                return None;
            }
            // A non-zero exit (Vim's `:cq`) abandons the edit.
            status = child.wait() => return Some(match status {
                Ok(status) if status.success() => Outcome::Saved,
                _ => Outcome::Cancelled,
            }),
            // Undrained notifications would overflow the reader's backlog during a long edit.
            Some(notification) = sources.notifications.recv(), if deferred.is_none() => {
                absorb_notification(app, client, sources, notification, deferred);
            }
        }
    }
}

/// Ask the editor to quit so it restores the terminal and drops its swap file, then force it.
async fn stop(child: &mut tokio::process::Child) {
    #[cfg(unix)]
    if let Some(pid) = child.id().and_then(|pid| libc::pid_t::try_from(pid).ok()) {
        unsafe { libc::kill(pid, libc::SIGTERM) };
    }
    if tokio::time::timeout(EDITOR_EXIT_GRACE, child.wait())
        .await
        .is_err()
    {
        let _ = child.kill().await;
    }
}

fn show_error(app: &mut App, message: String) {
    app.show_toast(message, ToastSeverity::Error, FAILURE_TOAST_SECS);
}
