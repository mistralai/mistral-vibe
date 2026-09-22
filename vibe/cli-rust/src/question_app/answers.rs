//! Recording an answer and replying to the blocked `callback/result`.

use std::sync::atomic::Ordering;
use std::sync::Arc;

use crate::server::Client;
use crate::server::{method, UserAnswer, UserQuestionResult};
use serde_json::json;

use super::{
    current_question, is_other_selected, is_submit_selected, is_within_grace_period,
    other_option_idx, other_text, switch_question,
};
use crate::app::App;

/// Enter outside the free-text row (Python `action_select`).
pub fn select(app: &mut App, client: &Arc<Client>) {
    if is_within_grace_period(app) {
        return;
    }
    if !current_question(app).multi_select {
        save_current_answer(app);
        advance_or_submit(app, client);
        return;
    }
    if is_submit_selected(app) {
        save_current_answer(app);
        if app
            .question_app
            .answers
            .contains_key(&app.question_app.current_question_idx)
        {
            advance_or_submit(app, client);
        }
        return;
    }
    toggle_selection(app, app.question_app.selected_option);
}

/// Enter on the focused free-text row (Python `on_input_submitted`).
pub fn submit_other(app: &mut App, client: &Arc<Client>) {
    if current_question(app).multi_select {
        if let Some(other_idx) = other_option_idx(app) {
            toggle_selection(app, other_idx);
        }
        return;
    }
    if other_text(app, app.question_app.current_question_idx)
        .trim()
        .is_empty()
    {
        return;
    }
    save_current_answer(app);
    advance_or_submit(app, client);
}

pub fn toggle_selection(app: &mut App, option_idx: usize) {
    let selections = app
        .question_app
        .multi_selections
        .entry(app.question_app.current_question_idx)
        .or_default();
    if !selections.remove(&option_idx) {
        selections.insert(option_idx);
    }
}

/// Mark an option selected without toggling it off (multi-select mouse click).
pub fn select_option(app: &mut App, option_idx: usize) {
    app.question_app
        .multi_selections
        .entry(app.question_app.current_question_idx)
        .or_default()
        .insert(option_idx);
}

fn advance_or_submit(app: &mut App, client: &Arc<Client>) {
    if all_answered(app) {
        submit(app, client);
        return;
    }
    let current = app.question_app.current_question_idx;
    let len = app.question_app.questions.len();
    let Some(new_idx) = (current + 1..len)
        .chain(0..current)
        .find(|idx| !app.question_app.answers.contains_key(idx))
    else {
        return;
    };
    switch_question(app, new_idx);
}

fn all_answered(app: &App) -> bool {
    (0..app.question_app.questions.len()).all(|idx| app.question_app.answers.contains_key(&idx))
}

fn save_current_answer(app: &mut App) {
    if current_question(app).multi_select {
        save_multi_select_answer(app);
    } else {
        save_single_select_answer(app);
    }
}

/// Join every selected label, in index order, into one answer.
fn save_multi_select_answer(app: &mut App) {
    let idx = app.question_app.current_question_idx;
    let Some(selections) = app.question_app.multi_selections.get(&idx) else {
        return;
    };
    let options = &app.question_app.questions[idx].options;
    let other_idx = options.len();
    let other = other_text(app, idx).trim();
    let mut answers: Vec<&str> = Vec::new();
    let mut has_other_answer = false;
    for &selection in selections {
        if selection < options.len() {
            answers.push(&options[selection].label);
        } else if selection == other_idx && !other.is_empty() {
            answers.push(other);
            has_other_answer = true;
        }
    }
    if answers.is_empty() {
        return;
    }
    let answer = answers.join(", ");
    app.question_app
        .answers
        .insert(idx, (answer, has_other_answer));
}

fn save_single_select_answer(app: &mut App) {
    let idx = app.question_app.current_question_idx;
    if is_other_selected(app) {
        let other = other_text(app, idx).trim().to_owned();
        if !other.is_empty() {
            app.question_app.answers.insert(idx, (other, true));
        }
        return;
    }
    let label = current_question(app).options[app.question_app.selected_option]
        .label
        .clone();
    app.question_app.answers.insert(idx, (label, false));
}

fn submit(app: &mut App, client: &Arc<Client>) {
    let answers = app
        .question_app
        .questions
        .iter()
        .enumerate()
        .map(|(idx, question)| {
            let (answer, is_other) = app
                .question_app
                .answers
                .get(&idx)
                .cloned()
                .unwrap_or_default();
            UserAnswer {
                question: question.question.clone(),
                answer,
                is_other,
            }
        })
        .collect();
    respond(
        app,
        client,
        UserQuestionResult {
            answers,
            cancelled: false,
        },
    );
}

/// Esc (Python `action_cancel`): answer nothing and let the tool report it.
pub fn cancel(app: &mut App, client: &Arc<Client>) {
    if is_within_grace_period(app) {
        return;
    }
    respond(
        app,
        client,
        UserQuestionResult {
            answers: Vec::new(),
            cancelled: true,
        },
    );
}

/// Close the app and answer the callback the turn is blocked on.
fn respond(app: &mut App, client: &Arc<Client>, result: UserQuestionResult) {
    app.question_app.open = false;
    crate::terminal_notifier::restore_running(app);
    app.view.loading.end_action_required();
    let callback_id = std::mem::take(&mut app.question_app.callback_id);
    let Some(session_id) = app.session.session_id.clone() else {
        return;
    };
    let client = client.clone();
    let pending = app.commit_started();
    tokio::spawn(async move {
        let params = json!({
            "sessionId": session_id,
            "result": {
                "callbackId": callback_id,
                "output": {"type": "user_input", "result": result},
            },
        });
        if let Err(error) = client.request(method::CALLBACK_RESULT, params).await {
            tracing::warn!(%error, "failed to answer user_input callback");
        }
        pending.fetch_sub(1, Ordering::Relaxed);
    });
}
