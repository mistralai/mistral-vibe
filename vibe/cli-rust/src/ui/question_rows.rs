//! Static row builders for the question box: the option prefix, the submit row
//! and the hint row.

use super::question_layout::Row;
use super::{list_cursor, theme};
use crate::app::App;
use crate::hints::{self, action, key};
use crate::question_app::{
    current_question, is_submit_selected, other_editing, other_locked, other_typing,
};

/// `  1. ` / `  1. [x] `, or `› 1. ` on the last answer; the cursor is the row's bar.
pub(super) fn option_prefix(index: usize, multi: bool, selected: bool, current: bool) -> String {
    let number = index + 1;
    let marker = list_cursor::marker(current);
    if multi {
        let check = if selected { "[x]" } else { "[ ]" };
        return format!("{marker}{number}. {check} ");
    }
    format!("{marker}{number}. ")
}

/// Split the current-value marker opening `row` into its own marker-colored span.
pub(super) fn style_marker(row: &mut Row) {
    let Some((text, style)) = row.first_mut() else {
        return;
    };
    let Some(rest) = text.strip_prefix(list_cursor::CURRENT).map(str::to_owned) else {
        return;
    };
    let base = *style;
    *text = list_cursor::CURRENT.to_owned();
    *style = list_cursor::marker_style(base, true);
    row.insert(1, (rest, base));
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

/// The hint line for the current question; an editable free-text row owns ←→ and the letters.
pub(super) fn help_row(app: &App) -> Row {
    let typing = other_typing(app);
    let mut list = Vec::new();
    if app.question_app.questions.len() > 1 && !typing {
        list.push((key::LEFT_RIGHT, action::QUESTIONS));
    }
    list.push(match typing {
        true => (key::ARROWS, action::NAVIGATE),
        false => hints::NAVIGATE,
    });
    list.push(if current_question(app).multi_select {
        (key::SPACE_ENTER, action::TOGGLE)
    } else {
        hints::SELECT
    });
    if other_locked(app) {
        list.push(("e", action::EDIT));
    } else if typing {
        list.push((key::CTRL_J, action::NEWLINE));
    }
    list.push(match other_editing(app) {
        true => hints::BACK,
        false => hints::CANCEL,
    });
    super::hint_line::styled(&list)
}
