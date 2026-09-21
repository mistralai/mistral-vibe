//! `ask_user_question` bottom-app: a bordered box with optional question tabs, the
//! title, the option rows, a free-text row, an optional submit row, an optional
//! footer note and a hint. Mirrors Python's `QuestionApp` and its TCSS.

use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::Frame;

use super::question_layout::{draw_box, Row};
use super::{bottom_bar, loading, theme, transcript};
use crate::app::App;
use crate::question_app::{
    current_question, is_other_selected, is_submit_selected, other_option_idx, other_text,
    reconcile_scroll, submit_option_idx, visible_option_rows,
};
use crate::utils::text::wrap_hard;

/// Textual's `max-height: 70vh` on `#question-app`.
const MAX_HEIGHT_RATIO: (u16, u16) = (7, 10);

/// The two border columns plus the `padding: 0 1` of `#question-app`.
fn content_width(area: Rect) -> usize {
    area.width.saturating_sub(4) as usize
}

/// Draw the whole screen with the question app replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));

    let loading_height = if app.view.transcript.is_empty() { 3 } else { 2 };
    let (rows, option_rows) = rows(app, content_width(area));
    let max_height = (area.height * MAX_HEIGHT_RATIO.0 / MAX_HEIGHT_RATIO.1).max(2);
    let total_rows = rows.len().min(u16::MAX as usize) as u16;
    let box_height = total_rows.saturating_add(2).min(max_height);
    let chunks = Layout::vertical([
        Constraint::Min(1),
        Constraint::Length(loading_height),
        Constraint::Length(box_height),
        Constraint::Length(1),
    ])
    .split(area);

    let visible_rows = box_height.saturating_sub(2);
    let scroll = reconcile_scroll(
        app.question_app.viewport,
        app.question_app.selected_option,
        &option_rows,
        total_rows,
        visible_rows,
    );
    let box_y = chunks[2].y;
    app.question_app.viewport.offset = scroll;
    app.question_app.option_rows = visible_option_rows(&option_rows, box_y, scroll, visible_rows);

    transcript::draw(app, f, chunks[0]);
    loading::draw(app, f, chunks[1]);
    crate::mouse::register_region(app, chunks[2], crate::mouse::MouseTarget::Question);
    draw_box(app, f, chunks[2], &rows, scroll, total_rows);
    bottom_bar::draw(app, f, chunks[3]);
}

/// Every content row of the box, in Textual compose order with its margins, plus
/// the `(row offset, option index)` hit map of the selectable rows.
fn rows(app: &App, width: usize) -> (Vec<Row>, Vec<(u16, usize)>) {
    let state = &app.question_app;
    let question = current_question(app);
    let multi_selected = state.multi_selections.get(&state.current_question_idx);
    let mut rows: Vec<Row> = Vec::new();
    let mut option_rows: Vec<(u16, usize)> = Vec::new();

    if state.questions.len() > 1 {
        rows.push(vec![(tabs(app), theme::text(theme::primary()))]);
        rows.push(Vec::new());
    }
    let title = theme::text(theme::primary()).add_modifier(Modifier::BOLD);
    push_wrapped(&mut rows, &question.question, title, width);
    rows.push(Vec::new());

    for (index, option) in question.options.iter().enumerate() {
        let focused = index == state.selected_option;
        let mut text = option_prefix(
            index,
            focused,
            question.multi_select,
            multi_selected.is_some_and(|selections| selections.contains(&index)),
        );
        text.push_str(&option.label);
        if !option.description.is_empty() {
            text.push_str(" - ");
            text.push_str(&option.description);
        }
        let start = rows.len() as u16;
        push_wrapped(&mut rows, &text, option_style(focused), width);
        option_rows.extend((start..rows.len() as u16).map(|row| (row, index)));
    }
    if let Some(other_idx) = other_option_idx(app) {
        option_rows.push((rows.len() as u16, other_idx));
        rows.push(other_row(app, other_idx));
    }
    if let Some(submit_idx) = submit_option_idx(app) {
        rows.push(Vec::new());
        option_rows.push((rows.len() as u16, submit_idx));
        rows.push(submit_row(app));
    }
    if let Some(note) = &state.footer_note {
        rows.push(Vec::new());
        push_wrapped(&mut rows, note, footer_note_style(), width);
    }
    rows.push(Vec::new());
    rows.push(help_row(app));
    (rows, option_rows)
}

/// Push `text` word-wrapped to `width`, one row per line, as a Textual `Static` does.
fn push_wrapped(rows: &mut Vec<Row>, text: &str, style: Style, width: usize) {
    rows.extend(
        wrap_hard(text, width)
            .into_iter()
            .map(|line| vec![(line, style)]),
    );
}

/// `› 1. ` / `  1. [x] ` (Python `_format_option_prefix`).
fn option_prefix(index: usize, focused: bool, multi: bool, selected: bool) -> String {
    let cursor = if focused { "› " } else { "  " };
    let number = index + 1;
    if multi {
        let check = if selected { "[x]" } else { "[ ]" };
        return format!("{cursor}{number}. {check} ");
    }
    format!("{cursor}{number}. ")
}

/// `.question-footer-note`: italic on truecolor themes, plain dim under `:ansi`.
fn footer_note_style() -> Style {
    if theme::is_ansi() {
        return theme::muted_style();
    }
    theme::text(theme::muted()).add_modifier(Modifier::ITALIC)
}

/// `.question-option` / `.question-option-selected`.
fn option_style(focused: bool) -> Style {
    if focused {
        theme::text(theme::primary()).add_modifier(Modifier::BOLD)
    } else {
        theme::text(theme::foreground())
    }
}

/// The free-text row: prefix, then the typed answer with its caret, or the
/// placeholder. `.question-other-prefix` keeps `$foreground` and only takes the
/// bold of `.question-option-selected` (it is the later TCSS rule).
fn other_row(app: &App, other_idx: usize) -> Row {
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
fn submit_row(app: &App) -> Row {
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

/// ` DB ✓   [Framework]` (Python `_update_tabs`).
fn tabs(app: &App) -> String {
    app.question_app
        .questions
        .iter()
        .enumerate()
        .map(|(index, question)| {
            let mut header = match question.header.is_empty() {
                true => format!("Q{}", index + 1),
                false => question.header.clone(),
            };
            if app.question_app.answers.contains_key(&index) {
                header.push_str(" ✓");
            }
            if index == app.question_app.current_question_idx {
                format!("[{header}]")
            } else {
                format!(" {header} ")
            }
        })
        .collect::<Vec<_>>()
        .join("  ")
}

/// The hint line: keys bold $primary, labels $text-muted.
fn help_row(app: &App) -> Row {
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
