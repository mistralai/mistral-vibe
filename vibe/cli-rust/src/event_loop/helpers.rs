//! Event-loop handlers and drawing helpers.

use std::io::Write;
use std::sync::Arc;
use std::time::Duration;

use anyhow::Result;
use crossterm::event::{Event, KeyEventKind, MouseEventKind};
use tokio::sync::mpsc;

use crate::app::{App, Status};
use crate::commands::{clear, simple, CommandEvent};
use crate::post_ready::{self, AccountReads};
use crate::server::Client;
use crate::transcript::local;
use crate::{config, event_handler, input};

use super::{HOLD_KEY, IDLE_MARKER, RELEASE_KEY};

pub(super) enum InputOutcome {
    Exit,
    Ignore,
    Marker(bool),
    Activity { reset_blink: bool },
    Redraw { reset_blink: bool },
}

/// Per-loop drawing and replay-marker state shared across the select arms.
pub(super) struct LoopState {
    pub redraw_pending: bool,
    pub real_event_pending: bool,
    pub idle_marker_emitted: bool,
    pub marker_held: bool,
}

impl LoopState {
    pub(super) fn new() -> Self {
        Self {
            redraw_pending: false,
            real_event_pending: false,
            idle_marker_emitted: false,
            marker_held: false,
        }
    }

    /// Fold one input event's outcome into the loop state.
    pub(super) fn apply_input(&mut self, outcome: InputOutcome, blink: &mut tokio::time::Interval) {
        match outcome {
            // Exit is handled by the caller before folding.
            InputOutcome::Exit => {}
            InputOutcome::Ignore => {}
            InputOutcome::Marker(held) => {
                self.marker_held = held;
                if !held {
                    self.redraw_pending = true;
                    self.real_event_pending = true;
                }
            }
            InputOutcome::Activity { reset_blink } => {
                if reset_blink {
                    blink.reset();
                }
                self.idle_marker_emitted = false;
                self.redraw_pending = true;
                self.real_event_pending = true;
            }
            InputOutcome::Redraw { reset_blink } => {
                if reset_blink {
                    blink.reset();
                }
                self.redraw_pending = true;
            }
        }
    }
}

pub(super) fn handle_input_event(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    event: Option<Event>,
    replaying: bool,
) -> InputOutcome {
    match event {
        Some(Event::Key(key))
            if replaying
                && matches!(
                    key.code,
                    crossterm::event::KeyCode::F(HOLD_KEY | RELEASE_KEY)
                ) =>
        {
            InputOutcome::Marker(key.code == crossterm::event::KeyCode::F(HOLD_KEY))
        }
        Some(Event::Key(key)) if key.kind == KeyEventKind::Press => {
            if input::request_suspend(app, &key) {
                return InputOutcome::Activity { reset_blink: false };
            }
            crate::mouse::cancel_capture(app);
            if dispatch_key(app, client, config_tx, key) {
                return InputOutcome::Exit;
            }
            app.set_app_focus(true);
            InputOutcome::Activity { reset_blink: true }
        }
        Some(Event::Mouse(mouse)) => {
            let reset_blink = matches!(mouse.kind, MouseEventKind::Down(_));
            if reset_blink {
                app.set_app_focus(true);
            }
            crate::mouse::handle(app, client, config_tx, mouse);
            InputOutcome::Activity { reset_blink }
        }
        Some(Event::Paste(text)) => {
            if app.trust.open {
                // Nothing to paste into before the session exists.
            } else if app.config_screen.open {
                config::handle_paste(app, text);
            } else {
                input::handle_paste(app, text);
            }
            InputOutcome::Activity { reset_blink: false }
        }
        Some(Event::Resize(_, _)) => InputOutcome::Activity { reset_blink: false },
        Some(Event::FocusGained) => {
            app.terminal_notifier.set_focus(true);
            app.set_app_focus(true);
            InputOutcome::Redraw { reset_blink: true }
        }
        Some(Event::FocusLost) => {
            app.terminal_notifier.set_focus(false);
            app.set_app_focus(false);
            crate::mouse::cancel_capture(app);
            app.view.mouse_position = None;
            InputOutcome::Redraw { reset_blink: false }
        }
        Some(_) => InputOutcome::Ignore,
        None => InputOutcome::Exit,
    }
}

pub(super) fn apply_command(app: &mut App, client: &Arc<Client>, command: CommandEvent) {
    app.commit_finished();
    let shell_completed = matches!(&command, CommandEvent::ShellCompleted { .. });
    if shell_completed || app.session.shell_operation_id.is_none() {
        app.view.command_loading = false;
    }
    let id = format!("rs-command-{}", app.view.transcript.revision());
    match command {
        CommandEvent::Result(text) => {
            local::add_command_result(&mut app.view.transcript, &id, &text)
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
            seed,
        } => {
            clear::apply_cleared(app, session_id.clone(), usage);
            if let Some(seed) = seed {
                clear::start_seed_turn(client, session_id, seed);
            }
        }
        CommandEvent::Compacted { state, status_id } => {
            crate::commands::compact::apply_manual_compacted(app, state, &status_id);
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

pub(super) fn step_scroll(app: &mut App, replaying: bool) -> bool {
    let view = &mut app.view;
    if replaying {
        view.scroll = view.scroll_target;
        return false;
    }
    view.scroll = crate::utils::scroll::ease_scroll(view.scroll, view.scroll_target);
    view.scroll != view.scroll_target
}

pub(super) fn draw_synchronized(
    terminal: &mut ratatui::DefaultTerminal,
    app: &mut App,
) -> Result<()> {
    use crossterm::terminal::{BeginSynchronizedUpdate, EndSynchronizedUpdate};

    let _ = crossterm::execute!(std::io::stdout(), BeginSynchronizedUpdate);
    let result = terminal.draw(|renderer| app.draw(renderer));
    let _ = crossterm::execute!(std::io::stdout(), EndSynchronizedUpdate);
    result?;
    crate::pointer::sync(app);
    crate::terminal_notifier::flush(&mut app.terminal_notifier);
    Ok(())
}

fn dispatch_key(
    app: &mut App,
    client: &Arc<Client>,
    config_tx: &mpsc::Sender<config::Loaded>,
    key: crossterm::event::KeyEvent,
) -> bool {
    if app.trust.open {
        crate::trust_folders::handle_key(app, key)
    } else if let Some(exit) = input::handle_priority_key(app, client, key) {
        exit
    } else if app.approval.open {
        crate::approval::handle_key(app, client, key);
        false
    } else if app.question_app.open {
        crate::question_input::handle_key(app, client, key);
        false
    } else if app.config_screen.open {
        config::handle_key(app, client, config_tx, key);
        false
    } else if app.resume_picker.open {
        crate::resume_picker::handle_key(app, client, key);
        false
    } else if app.mcp.open {
        input::handle_mcp_key(app, client, key);
        false
    } else if app.mcp_oauth.open {
        input::handle_mcp_oauth_key(app, client, key);
        false
    } else if app.connector_auth.open {
        input::handle_connector_auth_key(app, client, key);
        false
    } else if app.rewind.open {
        crate::rewind::handle_key(app, client, key);
        false
    } else {
        input::handle_key(app, client, config_tx, key)
    }
}

pub(super) fn preview_deadline(app: &App) -> tokio::time::Instant {
    app.theme_picker
        .preview_at
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(|| tokio::time::Instant::now() + Duration::from_secs(3600))
}

pub(super) fn spinner_deadline(app: &App) -> tokio::time::Instant {
    app.agents
        .spinner_at
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(|| tokio::time::Instant::now() + Duration::from_secs(3600))
}

pub(super) fn typing_pause_deadline(app: &App) -> tokio::time::Instant {
    crate::question_app::typing_pause_deadline(app)
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(tokio::time::Instant::now)
}

pub(super) fn approval_pause_deadline(app: &App) -> tokio::time::Instant {
    crate::approval::typing_pause_deadline(app)
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(tokio::time::Instant::now)
}

pub(super) fn feedback_deadline(app: &App) -> tokio::time::Instant {
    app.feedback
        .hide_at
        .map(tokio::time::Instant::from_std)
        .unwrap_or_else(|| tokio::time::Instant::now() + Duration::from_secs(3600))
}

pub(super) fn emit_idle_marker() {
    let mut out = std::io::stdout();
    let _ = out.write_all(IDLE_MARKER);
    let _ = out.flush();
}
