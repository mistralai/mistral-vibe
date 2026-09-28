//! `ask_user_question` bottom-app state, mirroring Python's `QuestionApp`.

mod answers;
mod scroll;

pub use answers::{cancel, select, select_option, submit_other, toggle_selection};
pub use scroll::{reconcile_scroll, visible_option_rows};

use std::sync::OnceLock;
use std::time::{Duration, Instant};

use crate::server::{UserQuestion, UserQuestionRequest};
use serde_json::Value;

use crate::app::App;

/// Keys buffered before the app appeared must not answer it (Python
/// `_INPUT_GRACE_PERIOD_S`).
pub const INPUT_GRACE_PERIOD: Duration = Duration::from_millis(500);

const DEFAULT_TYPING_DEBOUNCE_MS: u64 = 1000;
const TYPING_DEBOUNCE_ENV_VAR: &str = "VIBE_TYPING_GRACE_PERIOD_MS";

#[derive(Clone, Copy, Default)]
pub struct Viewport {
    pub offset: u16,
    pub detached: bool,
}

impl Viewport {
    pub fn follow_selection(&mut self) {
        self.detached = false;
    }

    pub fn detach_at(&mut self, offset: u16) {
        self.offset = offset;
        self.detached = true;
    }
}

/// How long the user must stop typing before a callback may take the input box
/// (Python `_resolve_typing_debounce_s`).
fn typing_debounce() -> Duration {
    static DEBOUNCE: OnceLock<Duration> = OnceLock::new();
    *DEBOUNCE.get_or_init(|| {
        let ms = std::env::var(TYPING_DEBOUNCE_ENV_VAR)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(DEFAULT_TYPING_DEBOUNCE_MS);
        Duration::from_millis(ms)
    })
}

/// When the pending callback may open, or `None` when none is waiting.
pub fn typing_pause_deadline(app: &App) -> Option<Instant> {
    let last = app.chat_input.last_keystroke?;
    app.question_app.pending.as_ref()?;
    Some(last + typing_debounce())
}

/// Take the input box once the user has paused (Python `_wait_for_typing_pause`).
pub fn show_pending(app: &mut App) {
    if typing_pause_deadline(app).is_some_and(|deadline| Instant::now() < deadline) {
        return;
    }
    let Some((callback_id, request)) = app.question_app.pending.take() else {
        return;
    };
    crate::terminal_notifier::action_required(app);
    let state = &mut app.question_app;
    state.open = true;
    state.callback_id = callback_id;
    state.questions = request.questions;
    state.footer_note = request.footer_note;
    state.current_question_idx = 0;
    state.selected_option = 0;
    state.viewport = Viewport::default();
    state.option_rows.clear();
    state.answers.clear();
    state.multi_selections.clear();
    state.other_texts.clear();
    state.other_cursor = 0;
    state.mount_time = Some(Instant::now());
}

/// Accept a `callback/call` of kind `user_input` (Python `_show_callback`). The
/// app opens straight away unless the user is mid-keystroke.
pub fn on_callback_call(app: &mut App, params: &Value) {
    let Some(callback) = params.get("callback") else {
        return;
    };
    if callback.pointer("/detail/kind").and_then(Value::as_str) != Some("user_input") {
        return;
    }
    let (Some(callback_id), Some(request)) = (
        callback.get("callbackId").and_then(Value::as_str),
        callback
            .pointer("/detail/request")
            .cloned()
            .and_then(|request| serde_json::from_value::<UserQuestionRequest>(request).ok()),
    ) else {
        tracing::warn!(?params, "user_input callback missing callbackId or request");
        return;
    };
    if request.questions.is_empty() {
        return;
    }
    if let Some(title) = callback.get("title").and_then(Value::as_str) {
        app.view.loading.begin_action_required(title);
    }
    app.question_app.pending = Some((callback_id.to_owned(), request));
    show_pending(app);
}

pub fn current_question(app: &App) -> &UserQuestion {
    &app.question_app.questions[app.question_app.current_question_idx]
}

pub fn has_other(app: &App) -> bool {
    !current_question(app).hide_other
}

pub fn total_options(app: &App) -> usize {
    let question = current_question(app);
    question.options.len() + usize::from(has_other(app)) + usize::from(question.multi_select)
}

/// Index of the free-text row, or `None` when the question hides it.
pub fn other_option_idx(app: &App) -> Option<usize> {
    has_other(app).then(|| current_question(app).options.len())
}

/// Index of the submit row, or `None` outside multi-select.
pub fn submit_option_idx(app: &App) -> Option<usize> {
    let question = current_question(app);
    question
        .multi_select
        .then(|| question.options.len() + usize::from(has_other(app)))
}

pub fn is_other_selected(app: &App) -> bool {
    other_option_idx(app) == Some(app.question_app.selected_option)
}

pub fn is_submit_selected(app: &App) -> bool {
    submit_option_idx(app) == Some(app.question_app.selected_option)
}

pub fn is_within_grace_period(app: &App) -> bool {
    app.question_app
        .mount_time
        .is_some_and(|at| at.elapsed() < INPUT_GRACE_PERIOD)
}

/// The free-text answer typed for `question_idx` (Python `_get_other_text`).
pub fn other_text(app: &App, question_idx: usize) -> &str {
    app.question_app
        .other_texts
        .get(&question_idx)
        .map_or("", String::as_str)
}

/// Move the cursor, parking the free-text caret at the end of its stored value.
pub fn set_selected_option(app: &mut App, option_idx: usize) {
    app.question_app.selected_option = option_idx;
    app.question_app.other_cursor = other_text(app, app.question_app.current_question_idx).len();
}

/// Move by keyboard and reattach the viewport to the selected option.
pub fn navigate_to_option(app: &mut App, option_idx: usize) {
    set_selected_option(app, option_idx);
    app.question_app.viewport.follow_selection();
}

pub fn move_up(app: &mut App) {
    let total = total_options(app);
    navigate_to_option(app, (app.question_app.selected_option + total - 1) % total);
}

pub fn move_down(app: &mut App) {
    let total = total_options(app);
    navigate_to_option(app, (app.question_app.selected_option + 1) % total);
}

pub fn next_question(app: &mut App) {
    if is_other_selected(app)
        && other_text(app, app.question_app.current_question_idx)
            .trim()
            .is_empty()
    {
        return;
    }
    let new_idx = (app.question_app.current_question_idx + 1) % app.question_app.questions.len();
    switch_question(app, new_idx);
}

pub fn prev_question(app: &mut App) {
    let len = app.question_app.questions.len();
    let new_idx = (app.question_app.current_question_idx + len - 1) % len;
    switch_question(app, new_idx);
}

pub(super) fn switch_question(app: &mut App, new_idx: usize) {
    app.question_app.current_question_idx = new_idx;
    navigate_to_option(app, restore_cursor(app, new_idx));
}

/// The option index to restore the cursor to for a previously answered question.
fn restore_cursor(app: &App, question_idx: usize) -> usize {
    let question = &app.question_app.questions[question_idx];
    if question.multi_select {
        return app
            .question_app
            .multi_selections
            .get(&question_idx)
            .and_then(|selections| selections.iter().min().copied())
            .unwrap_or(0);
    }
    let Some((answer_text, is_other)) = app.question_app.answers.get(&question_idx) else {
        return 0;
    };
    if *is_other {
        return question.options.len();
    }
    question
        .options
        .iter()
        .position(|option| &option.label == answer_text)
        .unwrap_or(0)
}
