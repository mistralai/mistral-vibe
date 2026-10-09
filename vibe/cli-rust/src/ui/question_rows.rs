//! Static row builders for the question box: the option prefix, the submit row
//! and the hint row.

use super::question_layout::Row;
use super::{list_cursor, theme};
use crate::app::App;
use crate::hints::{self, action, key};
use crate::question_app::{current_question, is_other_selected, is_submit_selected};

/// `  1. ` / `  1. [x] ` (Python `_format_option_prefix`); the cursor is the row's bar.
pub(super) fn option_prefix(index: usize, multi: bool, selected: bool) -> String {
    let number = index + 1;
    if multi {
        let check = if selected { "[x]" } else { "[ ]" };
        return format!("  {number}. {check} ");
    }
    format!("  {number}. ")
}

/// `     Submit →`, reading `Next` while other questions are still unanswered.
pub(super) fn submit_row(app: &App) -> Row {
    let focused = is_submit_selected(app);
    let answered: usize = (0..app.question_app.questions.len())
        .filter(|idx| {
            *idx == app.question_app.current_question_idx
                || app.question_app.answers.contains_key(idx)
        })
        .count();
    let text = if answered == app.question_app.questions.len() {
        "Submit"
    } else {
        "Next"
    };
    let style = match focused {
        true => list_cursor::style(),
        false => theme::text(theme::foreground()),
    };
    vec![(format!("     {text} →"), style)]
}

/// The hint line for the current question.
pub(super) fn help_row(app: &App) -> Row {
    let mut list = Vec::new();
    if app.question_app.questions.len() > 1 {
        list.push((key::LEFT_RIGHT, action::QUESTIONS));
    }
    list.push(hints::NAVIGATE);
    list.push(if current_question(app).multi_select {
        (key::SPACE_ENTER, action::TOGGLE)
    } else {
        hints::SELECT
    });
    if is_other_selected(app) {
        list.push((key::CTRL_J, action::NEWLINE));
    }
    list.push(hints::CANCEL);
    super::hint_line::styled(&list)
}
