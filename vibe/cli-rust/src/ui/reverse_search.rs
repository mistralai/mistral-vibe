//! The bottom-bar line of the Ctrl+R history search: prompt, query, caret and status.

use ratatui::layout::{Position, Rect};
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::{hint_line, theme};
use crate::app::App;
use crate::hints::{action, key, Hint};
use crate::mentions::Mention;
use crate::reverse_search::Status;

const PROMPT: &str = "reverse-i-search: ";
const NO_MATCH: &str = "no match";
const HINTS: &[Hint] = &[
    (key::ARROWS, action::NAVIGATE),
    (key::ENTER, action::ACCEPT),
    (key::ESC, action::CANCEL),
];

/// The query with newlines and tabs as visible markers.
fn shown_query(app: &App) -> Option<String> {
    let search = app.reverse_search.as_ref()?;
    Some(search.query.replace('\n', "↵").replace('\t', "⇥"))
}

/// The composer ranges matching the query, as spans the composer clamps per row.
pub fn found(app: &App) -> Vec<Mention> {
    crate::reverse_search::highlights(app)
        .into_iter()
        .map(|(start, end)| Mention {
            start,
            end,
            paste: None,
        })
        .collect()
}

/// A previewed match is reversed and bold, like a selection.
pub fn style(base: Style, found: bool) -> Style {
    match found {
        true => base.add_modifier(Modifier::REVERSED | Modifier::BOLD),
        false => base,
    }
}

/// The line that replaces the working directory while searching; its hints stay on it while they fit the `width`.
pub fn line(app: &App, width: u16) -> Option<Line<'static>> {
    let status = app.reverse_search.as_ref()?.status;
    let mut spans = vec![
        Span::styled(PROMPT, theme::muted_style()),
        Span::styled(shown_query(app)?, theme::text(theme::text_primary())),
    ];
    match status {
        Status::Idle => {}
        Status::Match if hints_below(app, width) => {}
        Status::Match => {
            spans.push(Span::raw("  "));
            spans.extend(hint_line::spans(HINTS));
        }
        Status::NoMatch => {
            spans.push(Span::raw("  "));
            spans.push(Span::styled(NO_MATCH, theme::text(theme::text_error())));
        }
    }
    Some(Line::from(spans))
}

/// Whether the match hints wrap under the search line instead of following the query.
fn hints_below(app: &App, width: u16) -> bool {
    let searching = app.quit.active().is_none()
        && app
            .reverse_search
            .as_ref()
            .is_some_and(|search| search.status == Status::Match);
    let query = shown_query(app).unwrap_or_default();
    let line = Line::from(format!("{PROMPT}{query}  ")).width() as u16;
    searching && line + crate::hints::width(HINTS) > width
}

/// The rows the bottom bar takes: its line, plus the match hints wrapped under it when they do not fit beside the query.
pub fn height(app: &App, width: u16) -> u16 {
    match hints_below(app, width) {
        true => 1 + hint_line::height(HINTS, width),
        false => 1,
    }
}

/// The match hints under the search line, when they do not fit beside the query.
pub fn draw_hints(app: &App, f: &mut Frame, area: Rect) {
    if hints_below(app, area.width) {
        hint_line::draw(f, area, HINTS);
    }
}

/// Draw the caret right after the query and park the terminal cursor on it.
pub fn draw_caret(app: &mut App, f: &mut Frame, area: Rect) {
    let Some(query) = shown_query(app) else {
        return;
    };
    if app.quit.active().is_some() || area.width == 0 {
        return;
    }
    let width = Line::from(format!("{PROMPT}{query}")).width() as u16;
    let x = area.x.saturating_add(width).min(area.right() - 1);
    let position = Position::new(x, area.y);
    if !app.main_input_cursor_on() {
        return;
    }
    if let Some(cell) = f.buffer_mut().cell_mut(position) {
        let style = theme::fixed::input_caret(cell.style());
        cell.set_style(style);
    }
    app.view.cursor_position = Some(position);
}
