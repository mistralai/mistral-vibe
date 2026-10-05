//! The pre-TUI update prompt (Python `setup/update_prompt`), run before the
//! main terminal owns the screen.

mod ansi;
mod view;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crossterm::event::{Event, KeyCode, KeyEventKind, KeyModifiers};
use ratatui::style::{Modifier, Style};

use crate::event_loop::{emit_idle_marker, HOLD_KEY, RELEASE_KEY};
use crate::ui::banner::petit_chat::PetitChat;
use crate::ui::theme;
use crate::update_notifier::gateway::UpdateSource;
use crate::update_notifier::update;
use crate::utils::is_replaying;

pub use ansi::color_span;
pub use view::draw;
pub use view::option_text;

/// Python `UpdatePromptMode`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UpdatePromptMode {
    Startup,
    CheckUpgrade,
}

/// Python `UpdateChoice`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum UpdateChoice {
    Update,
    Continue,
}

/// Python `UpdatePromptResult`; `Updated` and `UpdateFailed` carry what the
/// update commands actually printed, so the post-dialog lines can show the
/// real outcome (Python's bool loses that and its success line can claim an
/// upgrade that never happened).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum UpdatePromptResult {
    Continue,
    Updated(Vec<update::UpdateCommandOutcome>),
    UpdateFailed(Vec<update::UpdateCommandOutcome>),
    Quit,
}

impl UpdatePromptMode {
    /// Python `_CONTINUE_LABELS`.
    pub fn continue_label(self) -> &'static str {
        match self {
            Self::Startup => "Continue with current version",
            Self::CheckUpgrade => "Cancel upgrade",
        }
    }
}

impl UpdateChoice {
    pub fn label(self, mode: UpdatePromptMode) -> &'static str {
        match self {
            Self::Update => "Update now",
            Self::Continue => mode.continue_label(),
        }
    }

    /// Python's modulo cycle over two choices: either direction swaps.
    fn next(self) -> Self {
        match self {
            Self::Update => Self::Continue,
            Self::Continue => Self::Update,
        }
    }
}

/// The dialog's UI state, pure except for the update run itself.
#[derive(Clone, Debug)]
pub struct UpdatePromptState {
    pub current_version: String,
    pub latest_version: String,
    pub mode: UpdatePromptMode,
    /// The manager whose answer the offer carries; its command's outcome
    /// settles whether a run that upgraded nothing was a no-op.
    pub source: UpdateSource,
    pub selected: UpdateChoice,
    pub updating: bool,
}

impl UpdatePromptState {
    pub fn new(
        current_version: &str,
        latest_version: &str,
        mode: UpdatePromptMode,
        source: UpdateSource,
    ) -> Self {
        Self {
            current_version: current_version.to_owned(),
            latest_version: latest_version.to_owned(),
            mode,
            source,
            // Python starts on "Update now".
            selected: UpdateChoice::Update,
            updating: false,
        }
    }

    /// The cycleable choices in display order.
    pub fn choices(&self) -> [UpdateChoice; 2] {
        [UpdateChoice::Update, UpdateChoice::Continue]
    }

    /// Python `_move`: swap choices, ignored while updating.
    pub fn move_selection(&mut self) {
        if !self.updating {
            self.selected = self.selected.next();
        }
    }
}

/// Python `ask_update_prompt`: own the terminal, answer one prompt. A terminal
/// that cannot be taken over (piped stdout) falls back to Continue, like
/// Python's `app.run(inline=True) or UpdatePromptResult.CONTINUE`.
pub async fn ask_update_prompt(state: &UpdatePromptState) -> UpdatePromptResult {
    let Ok(mut terminal) = ratatui::try_init() else {
        return UpdatePromptResult::Continue;
    };
    let result = dialog_loop(&mut terminal, state).await;
    // Erase while the dialog still owns the screen: terminals that do not
    // track alternate screens (the e2e vt) would otherwise keep its frame
    // under the next app, which styles blank cells differently than it
    // paints them.
    let _ = terminal.clear();
    ratatui::restore();
    // `ratatui::restore` leaves the cursor hidden; give the shell it back.
    let _ = crossterm::execute!(
        std::io::stdout(),
        crossterm::cursor::Show,
        crossterm::terminal::SetTitle("")
    );
    result
}

/// Python `_show_update_prompt` (cli.py): run the dialog, then act on its
/// answer. Returns the exit code to take, or `None` to continue into the TUI.
/// A dismissed-update cache write failure is logged by
/// `mark_update_as_dismissed` with Python's `OSError` message.
pub async fn show_update_prompt(
    state: &UpdatePromptState,
    dismiss_on_continue: bool,
) -> Option<i32> {
    let result = ask_update_prompt(state).await;
    match result {
        UpdatePromptResult::Continue => {
            if dismiss_on_continue {
                crate::update_notifier::update::mark_update_as_dismissed(
                    &crate::update_notifier::cache::FileSystemUpdateCacheRepository,
                    &state.latest_version,
                );
            }
            None
        }
        UpdatePromptResult::Quit => Some(0),
        UpdatePromptResult::Updated(outcomes) => {
            // The manager's own answer can differ from the advertised latest
            // (a lagging brew formula, a re-resolved uv constraint); its
            // evidence line names the version the user actually got.
            let installed = update::upgraded_version(&outcomes, state.source)
                .unwrap_or_else(|| state.latest_version.clone());
            // A brew revision stays newer than `CARGO_PKG_VERSION` after the
            // upgrade; record the bare release so the next launch is quiet.
            update::record_revision_upgrade(
                &crate::update_notifier::cache::FileSystemUpdateCacheRepository,
                &state.latest_version,
            );
            println!(
                "{}\n  Run {} to start using the new version.",
                color_span(
                    theme::text(theme::success()),
                    &format!(
                        "✔ Vibe was updated from {} to {}.",
                        state.current_version, installed
                    )
                ),
                color_span(Style::default().add_modifier(Modifier::BOLD), "vibe")
            );
            Some(0)
        }
        UpdatePromptResult::UpdateFailed(outcomes) => {
            // One line, from what the package managers actually said: their
            // exit codes cannot tell a failed upgrade from a no-op one. The
            // offer's own manager settles the meaning — a no-op (its command
            // exited 0 without upgrading) is not an error, so orange; only
            // real failures print red.
            let no_op = update::run_was_no_op(&outcomes, state.source);
            let color = if no_op { theme::ORANGE } else { theme::error() };
            println!(
                "{}",
                color_span(
                    theme::text(color),
                    &format!(
                        "✗ Vibe was not updated: {}",
                        update::not_updated_reason(&outcomes, state.source)
                    )
                )
            );
            // A startup no-op — the offer's own manager exited 0 without
            // upgrading, e.g. the re-resolved version is not newer after all
            // — must not cost the user their session or re-offer the same
            // no-op on every launch: dismiss the offer and continue into the
            // TUI. Divergence from Python, which reports success on the same
            // exit 0 and exits without the TUI either; a real failure still
            // exits 1.
            if state.mode == UpdatePromptMode::Startup && no_op {
                crate::update_notifier::update::mark_update_as_dismissed(
                    &crate::update_notifier::cache::FileSystemUpdateCacheRepository,
                    &state.latest_version,
                );
                return None;
            }
            Some(1)
        }
    }
}

/// Python's cancel binding: Ctrl+C or Ctrl+Q quits, from either selection and
/// while updating.
fn is_quit_key(key: &crossterm::event::KeyEvent) -> bool {
    matches!(key.code, KeyCode::Char('c') | KeyCode::Char('q'))
        && key.modifiers.contains(KeyModifiers::CONTROL)
}

/// One key's effect on the dialog state, or the dialog's answer.
pub fn apply_key(
    state: &mut UpdatePromptState,
    key: crossterm::event::KeyEvent,
) -> Option<UpdatePromptResult> {
    if key.kind != KeyEventKind::Press {
        return None;
    }
    if is_quit_key(&key) {
        return Some(UpdatePromptResult::Quit);
    }
    match key.code {
        KeyCode::Left | KeyCode::Right if !state.updating => {
            state.move_selection();
            None
        }
        KeyCode::Enter if !state.updating => match state.selected {
            UpdateChoice::Continue => Some(UpdatePromptResult::Continue),
            UpdateChoice::Update => {
                state.updating = true;
                None
            }
        },
        _ => None,
    }
}

async fn dialog_loop(
    terminal: &mut ratatui::DefaultTerminal,
    state: &UpdatePromptState,
) -> UpdatePromptResult {
    let mut state = state.clone();
    let mut chat = PetitChat::default();
    let replaying = is_replaying();
    let mut marker_held = false;
    loop {
        view::draw(terminal, &state, &mut chat);
        if replaying && !marker_held {
            emit_idle_marker();
        }
        let event = match crossterm::event::read() {
            Ok(event) => event,
            // Python falls back to Continue when the dialog cannot run.
            Err(_) => return finish_step(replaying, UpdatePromptResult::Continue),
        };
        let Event::Key(key) = event else {
            continue;
        };
        if replaying && key.kind == KeyEventKind::Press {
            match key.code {
                KeyCode::F(HOLD_KEY) => {
                    marker_held = true;
                    continue;
                }
                KeyCode::F(RELEASE_KEY) => {
                    marker_held = false;
                    continue;
                }
                _ => {}
            }
        }
        if let Some(result) = apply_key(&mut state, key) {
            return finish_step(replaying, result);
        }
        if state.updating {
            // Python `enter_updating_state`: swap the options for the spinner,
            // then run the upgrade, cancellable like `action_quit_prompt`.
            view::draw(terminal, &state, &mut chat);
            if replaying {
                emit_idle_marker();
            }
            let result = run_update(terminal, &state, &mut chat).await;
            return finish_step(replaying, result);
        }
    }
}

/// Await the upgrade while animating the spinner, quitting on Ctrl+C/Q like
/// Python's cancel path: the running command is `_terminate`d (gentle stop
/// first, so a mid-install uv is not SIGKILLed half-way through) and the
/// terminated run settles before the dialog answers. The key reader is a
/// `spawn_blocking` task whose poll loop rechecks the stop flag every
/// `QUIT_POLL_INTERVAL`, so awaiting it after the run joins within one poll
/// cycle — leaving it alive would let it read and discard the terminal's
/// next key while the TUI starts.
async fn run_update(
    terminal: &mut ratatui::DefaultTerminal,
    state: &UpdatePromptState,
    chat: &mut PetitChat,
) -> UpdatePromptResult {
    let stop = Arc::new(AtomicBool::new(false));
    let reader_stop = Arc::clone(&stop);
    let mut quit_keys = tokio::task::spawn_blocking(move || quit_key_reader(&reader_stop));
    let (terminate_update, update_terminated) = tokio::sync::watch::channel(false);
    let update = update::do_update(&update_terminated);
    tokio::pin!(update);
    let mut frame = tokio::time::interval(crate::ui::banner::petit_chat::FRAME_INTERVAL);
    frame.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            outcomes = &mut update => {
                stop.store(true, Ordering::Relaxed);
                // Join the reader before the result travels: a startup
                // no-op continues into the TUI, and a reader still in its
                // poll loop could read and swallow that first key.
                let _ = (&mut quit_keys).await;
                return if update::any_command_upgraded(&outcomes, state.source) {
                    UpdatePromptResult::Updated(outcomes)
                } else {
                    UpdatePromptResult::UpdateFailed(outcomes)
                }
            }
            _ = &mut quit_keys => {
                // The reader answered or died; either way the run must be
                // wound down before quitting. The remaining commands are
                // skipped by the terminated flag inside `do_update`.
                let _ = terminate_update.send(true);
                break;
            }
            _ = frame.tick() => {
                chat.tick(Instant::now());
                view::draw(terminal, state, chat);
            }
        }
    }
    // Settling is bounded: the current command gets the terminate grace
    // window, then the hard kill; nothing else is started after it.
    let _ = (&mut update).await;
    stop.store(true, Ordering::Relaxed);
    UpdatePromptResult::Quit
}

/// How long the quit-key reader waits for an event before rechecking the
/// stop flag. Small enough that the reader's exit after the dialog goes
/// unnoticed, large enough not to busy-loop.
const QUIT_POLL_INTERVAL: Duration = Duration::from_millis(100);

/// Poll for a quit key until one arrives, the terminal errors, or `stop` is
/// set. Returns whether a quit key was pressed. Public for the stop-flag
/// exit test; the poll loop never touches the terminal once stopped.
pub fn quit_key_reader(stop: &AtomicBool) -> bool {
    loop {
        if stop.load(Ordering::Relaxed) {
            return false;
        }
        match crossterm::event::poll(QUIT_POLL_INTERVAL) {
            Ok(true) => match crossterm::event::read() {
                Ok(Event::Key(key)) if key.kind == KeyEventKind::Press && is_quit_key(&key) => {
                    return true
                }
                Ok(_) => {}
                Err(_) => return false,
            },
            Ok(false) => {}
            Err(_) => return false,
        }
    }
}

/// Close a replay step cleanly: swallow the trailing release of the batch that
/// answered the prompt, so the main TUI never reads the dialog's leftover
/// marker keys. No marker is emitted here on purpose: the step settles on the
/// next surface's own first marker (the TUI's Ready redraw); an exit into a
/// surface that owes none would hang the harness instead.
fn finish_step(replaying: bool, result: UpdatePromptResult) -> UpdatePromptResult {
    if replaying {
        drain_replay_release();
    }
    result
}

/// Consume the pending F24, if it lands within the short window the harness's
/// single-write batches make available.
fn drain_replay_release() {
    let deadline = Instant::now() + Duration::from_millis(50);
    while Instant::now() < deadline {
        if !crossterm::event::poll(Duration::from_millis(5)).unwrap_or(false) {
            return;
        }
        match crossterm::event::read() {
            Ok(Event::Key(key)) if key.code == KeyCode::F(RELEASE_KEY) => return,
            Ok(_) => continue,
            Err(_) => return,
        }
    }
}
