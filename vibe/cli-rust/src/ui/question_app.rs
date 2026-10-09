//! `ask_user_question` bottom-app: a bordered box with optional question tabs, the
//! title, the option rows, a free-text row, an optional submit row, an optional
//! footer note and a hint. Mirrors Python's `QuestionApp` and its TCSS.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::Frame;

use super::bottom_app::Kind;
use super::question_layout::{chrome_gaps, draw_box, visible_folds, Row};
use super::question_other::{self, other_rows};
use super::question_rows::{help_row, option_prefix, submit_row};
use super::theme;
use crate::app::App;
use crate::question_app::{
    current_question, other_option_idx, reconcile_scroll, submit_option_idx, visible_option_rows,
    OtherField,
};
use crate::selection::fold;

/// Textual's `max-height: 70vh` on `#question-app`.
const MAX_HEIGHT_RATIO: (u16, u16) = (7, 10);

/// The `(row offset, option index)` hit map of the selectable rows.
type OptionRows = Vec<(u16, usize)>;
/// The `(row offset, column, width)` chrome cells: option prefixes and scroll arrows.
type ChromeRows = Vec<(u16, u16, u16)>;
/// The `(row offset, gap)` of every row continuing a wrapped line.
type FoldRows = Vec<(u16, u16)>;

/// The two border columns plus the `padding: 0 1` of `#question-app`, split
/// evenly left/right of the content.
const BORDER_WIDTH: u16 = 4;

fn content_width(area: Rect) -> usize {
    area.width.saturating_sub(BORDER_WIDTH) as usize
}

/// Draw the whole screen with the question app replacing the input box.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let text_width =
        other_option_idx(app).map(|idx| question_other::text_width(app, idx, content_width(area)));
    if let Some(text_width) = text_width {
        question_other::follow_caret(app, text_width);
    }
    let (rows, option_rows, chrome, folds) = rows(app, content_width(area));
    let max_height = (area.height * MAX_HEIGHT_RATIO.0 / MAX_HEIGHT_RATIO.1).max(2);
    let total_rows = rows.len().min(u16::MAX as usize) as u16;
    let box_height = total_rows.saturating_add(2).min(max_height);
    let visible_rows = box_height.saturating_sub(2);
    let draw = |app: &mut App, f: &mut Frame, area: Rect| {
        let scroll = reconcile_scroll(
            app.question_app.viewport,
            app.question_app.selected_option,
            &option_rows,
            total_rows,
            visible_rows,
        );
        app.question_app.viewport.offset = scroll;
        app.question_app.option_rows =
            visible_option_rows(&option_rows, area.y, scroll, visible_rows);
        let content = super::bottom_app::content(area);
        app.question_app.other_field = other_field(app, &option_rows, content, scroll, text_width);
        app.view.bottom_app_selection_chrome = chrome_gaps(&chrome, content, scroll, visible_rows);
        app.view.bottom_app_selection_folds = visible_folds(&folds, content, scroll, visible_rows);
        draw_box(app, f, area, &rows, scroll, total_rows);
    };
    super::bottom_app::draw(app, f, area, box_height, Kind::Question, draw);
}

/// Where the free-text field's first wrapped row lands on screen, for click-to-caret.
fn other_field(
    app: &App,
    option_rows: &OptionRows,
    content: Rect,
    scroll: u16,
    text_width: Option<u16>,
) -> Option<OtherField> {
    let other_idx = other_option_idx(app)?;
    let &(row, _) = option_rows.iter().find(|(_, idx)| *idx == other_idx)?;
    let top = i32::try_from(app.question_app.other_scroll).unwrap_or(i32::MAX);
    Some(OtherField {
        x: content.right().saturating_sub(text_width?),
        y: i32::from(content.y) + i32::from(row) - i32::from(scroll) - top,
        text_width: text_width?,
    })
}

/// Every content row of the box, in Textual compose order with its margins, the
/// `(row offset, option index)` hit map of the selectable rows, the
/// `(row offset, column, width)` chrome of the prefixes and scroll arrows, and the folds.
fn rows(app: &App, width: usize) -> (Vec<Row>, OptionRows, ChromeRows, FoldRows) {
    let state = &app.question_app;
    let question = current_question(app);
    let multi_selected = state.multi_selections.get(&state.current_question_idx);
    let mut rows: Vec<Row> = Vec::new();
    let mut option_rows: OptionRows = Vec::new();
    let mut chrome: ChromeRows = Vec::new();
    let mut folds: FoldRows = Vec::new();

    if state.questions.len() > 1 {
        rows.push(vec![(tabs(app), theme::text(theme::primary()))]);
        rows.push(Vec::new());
    }
    let title = theme::text(theme::primary()).add_modifier(Modifier::BOLD);
    push_wrapped(&mut rows, &mut folds, &question.question, title, width);
    rows.push(Vec::new());

    for (index, option) in question.options.iter().enumerate() {
        let focused = index == state.selected_option;
        let mut text = option_prefix(
            index,
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
        chrome.push((start, 0, prefix_width));
        push_wrapped(&mut rows, &mut folds, &text, option_style(focused), width);
        option_rows.extend((start..rows.len() as u16).map(|row| (row, index)));
    }
    if let Some(other_idx) = other_option_idx(app) {
        for (row, arrow, gap) in other_rows(app, other_idx, width) {
            let at = rows.len() as u16;
            chrome.push((at, 0, row[0].0.chars().count() as u16));
            if arrow {
                chrome.push((at, (width as u16).saturating_sub(1), 1));
            }
            if let Some(gap) = gap {
                folds.push((at, gap));
            }
            option_rows.push((at, other_idx));
            rows.push(row);
        }
    }
    if let Some(submit_idx) = submit_option_idx(app) {
        rows.push(Vec::new());
        option_rows.push((rows.len() as u16, submit_idx));
        rows.push(submit_row(app));
    }
    if let Some(note) = &state.footer_note {
        rows.push(Vec::new());
        push_wrapped(&mut rows, &mut folds, note, footer_note_style(), width);
    }
    rows.push(Vec::new());
    rows.push(help_row(app));
    (rows, option_rows, chrome, folds)
}

/// Push `text` word-wrapped to `width`, one row per line, as a Textual `Static` does.
fn push_wrapped(rows: &mut Vec<Row>, folds: &mut FoldRows, text: &str, style: Style, width: usize) {
    for (line, gap) in fold::wrap_hard(text, width) {
        if let Some(gap) = gap {
            folds.push((rows.len() as u16, gap));
        }
        rows.push(vec![(line, style)]);
    }
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
        super::list_cursor::style()
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
