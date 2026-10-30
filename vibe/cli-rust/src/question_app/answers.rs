//! Recording an answer and replying to the blocked `callback/result`.

use std::sync::Arc;

use crate::server::Client;
use crate::server::{
    method, CallbackAddress, CallbackOutput, UserAnswer, UserQuestionRequest, UserQuestionResult,
};

use super::{
    callback_active, current_question, delivered, is_other_selected, is_submit_selected,
    is_within_grace_period, other_option_idx, other_text, show_pending, switch_question,
    QuestionSource,
};
use crate::app::{App, ToastSeverity};

/// The server's reply to a question's `callback/result`; `retry` reopens a rejected answer.
pub struct Event {
    pub reply: Result<(), String>,
    pub retry: Option<(CallbackAddress, UserQuestionRequest)>,
}

/// Enter outside the free-text row.
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
    toggle_selection(app, app.question_app.list.selected());
}

/// Space on a multi-select option.
pub fn toggle_focused(app: &mut App) {
    if is_within_grace_period(app) {
        return;
    }
    if !current_question(app).multi_select || is_submit_selected(app) {
        return;
    }
    toggle_selection(app, app.question_app.list.selected());
}

/// Enter on the focused free-text row.
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

fn toggle_selection(app: &mut App, option_idx: usize) {
    let selections = app
        .question_app
        .multi_selections
        .entry(app.question_app.current_question_idx)
        .or_default();
    if !selections.remove(&option_idx) {
        selections.insert(option_idx);
    }
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
    let label = current_question(app).options[app.question_app.list.selected()]
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

/// Drop the question from `source` when its operation ended unanswered.
pub fn dismiss(app: &mut App, source: &QuestionSource) {
    let question = &mut app.question_app;
    question.pending.retain(|(pending, _)| pending != source);
    if question.open && question.source.as_ref() == Some(source) {
        question.source = None;
        close(app);
        show_pending(app);
        settle_loading(app);
    }
}

/// Hide the app without answering; the caller owns `source`.
fn close(app: &mut App) {
    app.question_app.open = false;
    app.view.bottom_app_selection_chrome.clear();
    app.view.bottom_app_selection_folds = crate::selection::Folds::default();
    crate::selection::clear_region(app, crate::selection::RegionId::BottomApp);
    crate::selection::clear_region(app, crate::selection::RegionId::Loading);
}

/// Resume the loading line once no callback waits on the user or on its answer.
fn settle_loading(app: &mut App) {
    if !callback_active(app) && app.approval.active.is_none() && !app.approval.has_pending() {
        app.view.loading.end_action_required();
    }
}

/// Esc: answer nothing and let the tool report it.
pub fn cancel(app: &mut App, client: &Arc<Client>) {
    if is_within_grace_period(app) {
        return;
    }
    crate::telemetry::user_cancelled_action(app, "cancel_question");
    respond(
        app,
        client,
        UserQuestionResult {
            answers: Vec::new(),
            cancelled: true,
        },
    );
}

/// Close the app, open the next question, and answer the callback the turn is blocked on.
fn respond(app: &mut App, client: &Arc<Client>, result: UserQuestionResult) {
    let source = app.question_app.source.take();
    let request = UserQuestionRequest {
        questions: app.question_app.questions.clone(),
        footer_note: app.question_app.footer_note.clone(),
    };
    close(app);
    show_pending(app);
    let address = match source {
        Some(QuestionSource::Callback(address)) => address,
        Some(QuestionSource::TeleportPush) => {
            settle_loading(app);
            return crate::teleport::answer_push(app, client, &result);
        }
        None => return settle_loading(app),
    };
    let retry = (!result.cancelled).then(|| (address.clone(), request));
    let output = CallbackOutput::UserInput { result };
    let Some(params) = address.result_params(app.session.session_id.as_deref(), &output) else {
        tracing::error!(callback_id = address.callback_id, "question has no session");
        return settle_loading(app);
    };
    let client = client.clone();
    crate::ui_event::spawn(app, async move {
        let reply = client.request(method::CALLBACK_RESULT, params).await;
        Event {
            reply: reply.map(drop).map_err(|error| format!("{error:#}")),
            retry,
        }
    });
}

/// Show a rejected answer and reopen its question; a rejected cancel is dropped so a dead callback cannot trap the user.
pub fn apply_event(app: &mut App, event: Event) {
    let Err(error) = event.reply else {
        return settle_loading(app);
    };
    tracing::warn!(error, "failed to answer user_input callback");
    app.show_toast(
        format!("Failed to answer question: {error}"),
        ToastSeverity::Warning,
        crate::approval::RESPONSE_ERROR_TOAST_SECS,
    );
    let Some((address, request)) = event.retry else {
        return settle_loading(app);
    };
    let source = QuestionSource::Callback(address);
    if delivered(app, &source) {
        return settle_loading(app);
    }
    app.question_app.pending.push_front((source, request));
    show_pending(app);
}
