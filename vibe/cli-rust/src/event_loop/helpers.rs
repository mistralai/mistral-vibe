//! Event-loop handlers and drawing helpers.

use std::io::Write;
use std::sync::Arc;

use crate::app::{App, Status};
use crate::commands::{clear, simple, CommandEvent};
use crate::completion_manager;
use crate::event_handler;
use crate::post_ready::{self, AccountReads};
use crate::server::Client;
use crate::transcript::local;
use anyhow::Result;

use super::IDLE_MARKER;

pub(super) fn apply_command(app: &mut App, client: &Arc<Client>, command: CommandEvent) {
    app.commit_finished();
    let shell_completed = matches!(&command, CommandEvent::ShellCompleted { .. });
    if shell_completed || app.session.shell_operation_id.is_none() {
        app.view.command_loading = false;
    }
    let id = format!("rs-command-{}", app.view.transcript.revision());
    match command {
        CommandEvent::RemoteProject(event) => {
            crate::vibe_code_project::apply_event(app, client, *event)
        }
        CommandEvent::Teleport(reply) => crate::teleport::apply_reply(app, client, *reply),
        CommandEvent::Plugins(event) => crate::plugins::apply_event(app, *event),
        CommandEvent::Result(text) => {
            local::add_command_result(&mut app.view.transcript, &id, &text)
        }
        CommandEvent::AgentStatistics(text) => {
            crate::commands::provider_auth::add_agent_statistics(app, &id, &text)
        }
        CommandEvent::Renamed(title) => {
            app.terminal_notifier.set_default_title(&title);
            local::add_command_result(
                &mut app.view.transcript,
                &id,
                &format!("Session renamed to \"{title}\"."),
            );
        }
        CommandEvent::Error(text) => local::add_command_error(&mut app.view.transcript, &id, &text),
        CommandEvent::Runtime(runtime, text) => {
            event_handler::apply_runtime_value(app, &runtime);
            local::add_status(&mut app.view.transcript, &id, &text);
        }
        CommandEvent::VoiceSettings {
            runtime,
            previous_enabled,
            enabling_audio,
        } => {
            crate::voice_app::apply_saved(app, &runtime, previous_enabled, enabling_audio);
        }
        CommandEvent::ProxySettings(result) => crate::proxy_setup::apply_read(app, result),
        CommandEvent::PostReady { reads, greeting } => {
            let plan = reads
                .as_ref()
                .and_then(|reads| post_ready::plan_title(&reads.account));
            cache_reads(app, reads);
            app.view.banner.set_account(plan, greeting);
        }
        CommandEvent::UntrustedConfig(warning) => {
            if let Some(text) = warning {
                local::add_warning(&mut app.view.transcript, &id, &text);
            }
        }
        CommandEvent::Cleared {
            session_id,
            usage,
            child_sessions,
            previous_session_id,
            seed,
        } => {
            clear::apply_cleared(app, session_id.clone(), usage, child_sessions);
            simple::add_text(
                app,
                &clear::new_conversation_text(previous_session_id.as_deref()),
            );
            if let Some(seed) = seed {
                local::add_message(
                    &mut app.view.transcript,
                    &seed.message_id,
                    "user",
                    &seed.text,
                );
                clear::start_seed_turn(app, client, session_id, seed);
            }
        }
        CommandEvent::Compacted { state, status_id } => {
            crate::commands::compact::apply_manual_compacted(app, *state, &status_id);
        }
        CommandEvent::CompactError { status_id, error } => {
            // Failed compactions never see session/compacted either.
            crate::commands::compact::settle_compact(app);
            local::settle_compact_status(&mut app.view.transcript, &status_id, Some(&error));
        }
        CommandEvent::RetryStarted => {}
        CommandEvent::RetryFailed { error } => {
            // No turn/completed will arrive; clear the busy state (Python _finalize_turn_ui).
            app.set_status(Status::Ready);
            // The interrupted retry never started, so no later turn inherits its interrupt.
            app.queue.interrupt_on_start = None;
            // Python keeps the retry presentation after a failed attempt; re-offer /retry.
            app.session.can_retry = true;
            local::add_command_error(&mut app.view.transcript, &id, &error);
        }
        CommandEvent::Whoami(reads) => {
            let text = simple::whoami_text(reads.as_ref().unwrap_or(&AccountReads::default()));
            cache_reads(app, reads);
            local::add_command_result(&mut app.view.transcript, &id, &text);
        }
        CommandEvent::ShellCompleted {
            operation_id,
            error,
        } => {
            if app.session.shell_operation_id.as_deref() == Some(operation_id.as_str()) {
                app.session.shell_operation_id = None;
                app.session.shell_started_at = None;
                crate::terminal_notifier::restore_running(app);
                app.session.shell_request_started = false;
                app.session.shell_interrupt_requested = false;
            }
            if let Some(error) = error {
                local::add_command_error(&mut app.view.transcript, &id, &error);
            }
        }
    }
}

fn cache_reads(app: &mut App, reads: Option<AccountReads>) {
    let Some(reads) = reads else {
        return;
    };
    let model = app.session.startup_config.active_model.clone();
    app.whoami.store(model, reads);
}

pub(super) fn step_scroll(app: &mut App, replaying: bool) {
    let view = &mut app.view;
    if replaying {
        view.scroll = view.scroll_target;
        return;
    }
    view.scroll = crate::utils::scroll::ease_scroll(view.scroll, view.scroll_target);
}

pub(super) fn draw_synchronized(terminal: &mut crate::terminal::Tui, app: &mut App) -> Result<()> {
    use crossterm::SynchronizedUpdate;

    std::io::stdout().sync_update(|_| crate::terminal::draw(terminal, app))??;
    crate::pointer::sync(app);
    crate::terminal_notifier::flush(&mut app.terminal_notifier);
    Ok(())
}

// `pub`, not `pub(super)`: `update_prompt` re-exports it through
// `event_loop` and the idle marker must reach the startup update dialog.
pub fn emit_idle_marker() {
    let mut out = std::io::stdout();
    let _ = out.write_all(IDLE_MARKER);
    let _ = out.flush();
}

/// One steady-loop frame: sync the file index, advance scroll and history,
/// paint, and kick the older-history page load. Returns whether an animation
/// is still easing (checked after the draw — the draw may set a new glide
/// target for an expansion scrolling into view), whether a history mount is
/// still in flight (both owe another frame), and whether the file index is
/// still working (it defers the replay marker).
#[allow(clippy::too_many_arguments)]
pub(super) fn redraw_frame(
    app: &mut App,
    client: &Arc<Client>,
    files: &mut tokio::sync::watch::Receiver<u64>,
    terminal: &mut crate::terminal::Tui,
    replaying: bool,
) -> Result<(bool, bool, bool)> {
    completion_manager::sync_files(app, files);
    let mounting = app
        .view
        .transcript_cache
        .advance_history(&app.view.transcript);
    step_scroll(app, replaying);
    draw_synchronized(terminal, app)?;
    // The draw may set a new glide target (an expansion scrolling into view).
    let animating = app.view.scroll != app.view.scroll_target;
    crate::older_history::load_older_history_page(app, client);
    let indexing = *files.borrow() == 0 && completion_manager::active_is_file(app);
    Ok((animating, mounting, indexing))
}

/// Emit the replay harness's idle marker for a settled frame, or reset its
/// one-shot while work is still in flight. `settle_busy` widens the settle
/// test to in-flight turns (the worktree gate's captures); `animating` keeps
/// the marker withheld while a glide (an expansion scrolling into view) is
/// still easing toward its target.
#[allow(clippy::too_many_arguments)]
pub(super) fn replay_marker(
    app: &App,
    state: &mut super::keys::LoopState,
    replaying: bool,
    settle_busy: bool,
    animating: bool,
    mounting: bool,
    indexing: bool,
) {
    if !replaying || !state.real_event_pending || state.marker_held || indexing {
        return;
    }
    let settled = if settle_busy {
        app.is_settled()
    } else {
        app.is_idle()
    };
    let settled = settled && !mounting;
    if !settled
        || animating
        || crate::selection::is_auto_scrolling(app)
        || crate::mouse::is_track_paging(app)
    {
        state.idle_marker_emitted = false;
    } else if !state.idle_marker_emitted {
        emit_idle_marker();
        state.idle_marker_emitted = true;
    }
}
