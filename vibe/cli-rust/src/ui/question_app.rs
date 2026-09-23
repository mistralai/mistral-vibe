//! `ask_user_question` bottom-app: a bordered box with optional question tabs, the
//! title, the option rows, a free-text row, an optional submit row, an optional
//! footer note and a hint. Mirrors Python's `QuestionApp` and its TCSS.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::Frame;

use super::question_layout::{draw_box, prefix_gaps, Row};
use super::question_rows::{help_row, option_prefix, other_row, submit_row};
use super::{bottom_bar, theme, transcript};
use crate::app::App;
use crate::question_app::{
    current_question, other_option_idx, reconcile_scroll, submit_option_idx, visible_option_rows,
};
use crate::utils::text::wrap_hard;

/// Textual's `max-height: 70vh` on `#question-app`.
const MAX_HEIGHT_RATIO: (u16, u16) = (7, 10);

/// The `(row offset, option index)` hit map of the selectable rows.
type OptionRows = Vec<(u16, usize)>;
/// The `(row offset, prefix width)` chrome of every option row's prefix.
type PrefixRows = Vec<(u16, u16)>;

/// The two border columns plus the `padding: 0 1` of `#question-app`, split
/// evenly left/right of the content.
const BORDER_WIDTH: u16 = 4;

fn content_width(area: Rect) -> usize {
    area.width.saturating_sub(BORDER_WIDTH) as usize
}

/// Draw the whole screen with the question app replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut()
        .set_style(area, Style::default().bg(theme::background()));

    let loading_height = if app.view.transcript.is_empty() { 3 } else { 2 };
    let (rows, option_rows, prefixes) = rows(app, content_width(area));
    let max_height = (area.height * MAX_HEIGHT_RATIO.0 / MAX_HEIGHT_RATIO.1).max(2);
    let total_rows = rows.len().min(u16::MAX as usize) as u16;
    let box_height = total_rows.saturating_add(2).min(max_height);
    let chunks = super::bottom_app_chunks(app, area, loading_height, box_height);

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
    super::selection::overlay(app, f);
    super::draw_loading_area(app, f, chunks[1]);
    super::selection::loading_region(app, f, chunks[1]);
    crate::mouse::register_region(app, chunks[2], crate::mouse::MouseTarget::Question);
    let content = box_content(chunks[2]);
    app.view.question_selection_region = super::selection::frame_region(content);
    app.view.question_selection_chrome = prefix_gaps(&prefixes, content, scroll, visible_rows);
    draw_box(app, f, chunks[2], &rows, scroll, total_rows);
    super::todo::draw_row(app, f, chunks[4]);
    super::selection::overlay_region(app, f, crate::selection::RegionId::Question);
    bottom_bar::draw(app, f, chunks[3]);
}

/// The text rectangle inside a box border plus `0 1` padding (Python selects
/// everything its widgets paint, but never the container's border).
fn box_content(area: Rect) -> Rect {
    Rect {
        x: area.x.saturating_add(BORDER_WIDTH / 2),
        y: area.y.saturating_add(1),
        width: area.width.saturating_sub(BORDER_WIDTH),
        height: area.height.saturating_sub(2),
    }
}

/// Every content row of the box, in Textual compose order with its margins, the
/// `(row offset, option index)` hit map of the selectable rows, and the
/// `(row offset, prefix width)` chrome of every option row's prefix.
fn rows(app: &App, width: usize) -> (Vec<Row>, OptionRows, PrefixRows) {
    let state = &app.question_app;
    let question = current_question(app);
    let multi_selected = state.multi_selections.get(&state.current_question_idx);
    let mut rows: Vec<Row> = Vec::new();
    let mut option_rows: OptionRows = Vec::new();
    let mut prefixes: PrefixRows = Vec::new();

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
        let prefix_width = text.chars().count() as u16;
        text.push_str(&option.label);
        if !option.description.is_empty() {
            text.push_str(" - ");
            text.push_str(&option.description);
        }
        let start = rows.len() as u16;
        prefixes.push((start, prefix_width));
        push_wrapped(&mut rows, &text, option_style(focused), width);
        option_rows.extend((start..rows.len() as u16).map(|row| (row, index)));
    }
    if let Some(other_idx) = other_option_idx(app) {
        let row = other_row(app, other_idx);
        prefixes.push((rows.len() as u16, row[0].0.chars().count() as u16));
        option_rows.push((rows.len() as u16, other_idx));
        rows.push(row);
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
    (rows, option_rows, prefixes)
}

/// Push `text` word-wrapped to `width`, one row per line, as a Textual `Static` does.
fn push_wrapped(rows: &mut Vec<Row>, text: &str, style: Style, width: usize) {
    rows.extend(
        wrap_hard(text, width)
            .into_iter()
            .map(|line| vec![(line, style)]),
    );
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
