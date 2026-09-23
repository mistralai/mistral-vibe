//! Static row builders for the question box: the option prefix, the free-text
//! row, the submit row and the hint row.

use ratatui::style::{Color, Modifier, Style};

use super::question_layout::Row;
use super::theme;
use crate::app::App;
use crate::question_app::{current_question, is_other_selected, is_submit_selected, other_text};

/// `› 1. ` / `  1. [x] ` (Python `_format_option_prefix`).
pub(super) fn option_prefix(index: usize, focused: bool, multi: bool, selected: bool) -> String {
    let cursor = if focused { "› " } else { "  " };
    let number = index + 1;
    if multi {
        let check = if selected { "[x]" } else { "[ ]" };
        return format!("{cursor}{number}. {check} ");
    }
    format!("{cursor}{number}. ")
}

/// The free-text row: prefix, then the typed answer with its caret, or the
/// placeholder. `.question-other-prefix` keeps `$foreground` and only takes the
/// bold of `.question-option-selected` (it is the later TCSS rule).
pub(super) fn other_row(app: &App, other_idx: usize) -> Row {
    let question = current_question(app);
    let focused = is_other_selected(app);
    let selected = app
        .question_app
        .multi_selections
        .get(&app.question_app.current_question_idx)
        .is_some_and(|selections| selections.contains(&other_idx));
    let mut prefix_style = theme::text(theme::foreground());
    if focused {
        prefix_style = prefix_style.add_modifier(Modifier::BOLD);
    }
    let mut row = vec![(
        option_prefix(other_idx, focused, question.multi_select, selected),
        prefix_style,
    )];

    // Empty means the placeholder, whether from the static or the focused `Input`.
    let text = other_text(app, app.question_app.current_question_idx);
    let (body, body_style) = match text.is_empty() {
        true => ("Type your answer...", theme::muted_style()),
        false => (text, theme::text(theme::foreground())),
    };
    let caret = (focused && app.view.cursor_on).then_some(app.question_app.other_cursor);
    for (offset, ch) in body.char_indices() {
        let style = if caret == Some(offset) {
            caret_style(body_style)
        } else {
            body_style
        };
        row.push((ch.to_string(), style));
    }
    if caret.is_some_and(|caret| caret >= body.len()) {
        row.push((" ".into(), caret_style(body_style)));
    }
    row
}

/// The Textual `Input` caret: `$input-cursor-foreground` on
/// `$input-cursor-background`, which ANSI themes resolve to black on white.
/// Unlike the `TextArea` block this keeps the character under it readable.
fn caret_style(base: Style) -> Style {
    if theme::is_ansi() {
        return base.fg(Color::Black).bg(Color::Gray);
    }
    base.fg(theme::background()).bg(theme::input_cursor_bg())
}

/// `›   Submit →`, reading `Next` while other questions are still unanswered.
pub(super) fn submit_row(app: &App) -> Row {
    let focused = is_submit_selected(app);
    let cursor = if focused { "› " } else { "  " };
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
    let mut style = theme::text(theme::foreground());
    if focused {
        style = style.add_modifier(Modifier::BOLD);
    }
    vec![(format!("{cursor}   {text} →"), style)]
}

/// The hint line: keys bold $primary, labels $text-muted.
pub(super) fn help_row(app: &App) -> Row {
    let key = theme::text(theme::primary()).add_modifier(Modifier::BOLD);
    let label = theme::muted_style();
    let select = if current_question(app).multi_select {
        " toggle  "
    } else {
        " select  "
    };
    let mut row = Vec::new();
    if app.question_app.questions.len() > 1 {
        row.push(("←→".to_string(), key));
        row.push((" questions  ".to_string(), label));
    }
    row.push(("↑↓/jk".to_string(), key));
    row.push((" navigate  ".to_string(), label));
    row.push(("Enter".to_string(), key));
    row.push((select.to_string(), label));
    row.push(("Esc".to_string(), key));
    row.push((" cancel".to_string(), label));
    row
}
