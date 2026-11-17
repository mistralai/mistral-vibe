//! `ask_user_question` bottom-app: a bordered box with optional question tabs, the
//! title, the option rows, a free-text row, an optional submit row, an optional
//! footer note and a hint.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::Frame;
use unicode_width::UnicodeWidthStr;

use super::bottom_app::Kind;
use super::question_layout::{chrome_gaps, draw_box, visible_folds, Row};
use super::question_other::{self, other_rows};
use super::question_rows::{help_row, option_prefix, style_marker, submit_row};
use super::theme;
use crate::app::App;
use crate::question_app::{
    answered_option, current_question, other_option_idx, submit_option_idx, OtherField,
};
use crate::selection::fold;
use crate::utils::text::ellipsize;

/// The box takes at most 70% of the screen height.
const MAX_HEIGHT_RATIO: (u16, u16) = (7, 10);

/// The option on each content row, `None` for the rest.
type OptionRows = Vec<Option<usize>>;
/// The `(row offset, column, width)` chrome cells: option prefixes and scroll arrows.
type ChromeRows = Vec<(u16, u16, u16)>;
/// The `(row offset, gap)` of every row continuing a wrapped line.
type FoldRows = Vec<(u16, u16)>;

/// The two border columns plus the `padding: 0 1` of `#question-app`, split
/// evenly left/right of the content.
const BORDER_WIDTH: u16 = 4;

/// The spaces between two question tabs on the same row.
const TAB_GAP: &str = "  ";

/// The suffix of an answered question's tab.
const ANSWERED: &str = " ✓";

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
        let content = super::bottom_app::content(area);
        let scroll = app.question_app.list.layout(&option_rows, content) as u16;
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
    let row = option_rows.iter().position(|idx| *idx == Some(other_idx))?;
    let top = i32::try_from(app.question_app.other_scroll).unwrap_or(i32::MAX);
    Some(OtherField {
        x: content.right().saturating_sub(text_width?),
        y: i32::from(content.y) + i32::try_from(row).unwrap_or(i32::MAX) - i32::from(scroll) - top,
        text_width: text_width?,
    })
}

/// Every content row of the box, in Textual compose order with its margins, the
/// option on each row, selection chrome, and folds.
fn rows(app: &App, width: usize) -> (Vec<Row>, OptionRows, ChromeRows, FoldRows) {
    let state = &app.question_app;
    let question = current_question(app);
    let multi_selected = state.multi_selections.get(&state.current_question_idx);
    let mut rows: Vec<Row> = Vec::new();
    let mut option_rows: OptionRows = Vec::new();
    let mut chrome: ChromeRows = Vec::new();
    let mut folds: FoldRows = Vec::new();

    if state.questions.len() > 1 {
        for tab_row in tab_rows(app, width) {
            rows.push(vec![(tab_row, theme::text(theme::primary()))]);
        }
        rows.push(Vec::new());
    }
    let title = theme::text(theme::primary()).add_modifier(Modifier::BOLD);
    push_wrapped(&mut rows, &mut folds, &question.question, title, width);
    rows.push(Vec::new());

    let answered = answered_option(app, state.current_question_idx);
    for (index, option) in question.options.iter().enumerate() {
        let focused = index == state.list.selected();
        let current = answered == Some(index);
        let mut text = option_prefix(
            index,
            question.multi_select,
            multi_selected.is_some_and(|selections| selections.contains(&index)),
            current,
        );
        let prefix_width = text.chars().count() as u16;
        text.push_str(&option.label);
        if !option.description.is_empty() {
            text.push_str(" - ");
            text.push_str(&option.description);
        }
        let start = rows.len() as u16;
        option_rows.resize(rows.len(), None);
        chrome.push((start, 0, prefix_width));
        push_wrapped(&mut rows, &mut folds, &text, option_style(focused), width);
        if current {
            style_marker(&mut rows[usize::from(start)]);
        }
        option_rows.resize(rows.len(), Some(index));
    }
    if let Some(other_idx) = other_option_idx(app) {
        option_rows.resize(rows.len(), None);
        let prefix_width = question_other::prefix_width(app, other_idx) as u16;
        for (row, arrow, gap) in other_rows(app, other_idx, width) {
            let at = rows.len() as u16;
            chrome.push((at, 0, prefix_width));
            if arrow {
                chrome.push((at, (width as u16).saturating_sub(1), 1));
            }
            if let Some(gap) = gap {
                folds.push((at, gap));
            }
            option_rows.push(Some(other_idx));
            rows.push(row);
        }
    }
    if let Some(submit_idx) = submit_option_idx(app) {
        rows.push(Vec::new());
        option_rows.resize(rows.len(), None);
        rows.push(submit_row(app));
        option_rows.resize(rows.len(), Some(submit_idx));
    }
    if let Some(note) = &state.footer_note {
        rows.push(Vec::new());
        push_wrapped(&mut rows, &mut folds, note, footer_note_style(), width);
    }
    rows.push(Vec::new());
    rows.push(help_row(app));
    option_rows.resize(rows.len(), None);
    (rows, option_rows, chrome, folds)
}

/// Push `text` word-wrapped to `width`, one row per line.
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

/// The question tabs, e.g. ` DB ✓   [Framework]`, wrapped between tabs to fit `width`.
fn tab_rows(app: &App, width: usize) -> Vec<String> {
    let mut rows: Vec<String> = Vec::new();
    for tab in tabs(app, width) {
        match rows.last_mut() {
            Some(row) if row.width() + TAB_GAP.width() + tab.width() <= width => {
                row.push_str(TAB_GAP);
                row.push_str(&tab);
            }
            _ => rows.push(tab),
        }
    }
    rows
}

/// One label per question, the header ellipsized so its brackets and `✓` fit in `width`.
fn tabs(app: &App, width: usize) -> impl Iterator<Item = String> + '_ {
    app.question_app
        .questions
        .iter()
        .enumerate()
        .map(move |(index, question)| {
            let mut header = match question.header.is_empty() {
                true => format!("Q{}", index + 1),
                false => question.header.clone(),
            };
            let answered = app.question_app.answers.contains_key(&index);
            let check = if answered { ANSWERED } else { "" };
            header = ellipsize(&header, width.saturating_sub(2 + check.width()));
            header.push_str(check);
            if index == app.question_app.current_question_idx {
                format!("[{header}]")
            } else {
                format!(" {header} ")
            }
        })
}
