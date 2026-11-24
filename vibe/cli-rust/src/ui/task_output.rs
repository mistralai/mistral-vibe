//! A background task's output in the tasks window: its lines with every search match lit and the current one marked, a scrollbar, and the search row while searching.

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::theme;
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::selection::{Fold, Folds, RegionId};
use crate::tasks_panel::find::Hit;
use crate::tasks_panel::{OutputView, Row};

#[cfg(test)]
mod tests;

/// Whether the search row shows: while typing a search, or while one is kept.
fn searching(view: &OutputView) -> bool {
    view.search.focused || !view.search.query.is_empty()
}

/// Output columns in a `width` wide content area: two stay free for the scrollbar, so the wrap never changes when it shows.
fn text_width(width: u16) -> u16 {
    width.saturating_sub(2)
}

/// Where the output's text sits in `content`, above the search row while searching.
fn text_area(view: &OutputView, content: Rect) -> Rect {
    Rect {
        width: text_width(content.width),
        height: content.height.saturating_sub(u16::from(searching(view))),
        ..content
    }
}

/// Rows the last lines, and the search row while searching, need at `width` content columns, up to `cap`.
pub fn content_rows(view: &OutputView, width: u16, cap: u16) -> usize {
    let search = u16::from(searching(view));
    let limit = usize::from(cap.saturating_sub(search));
    let rows = view.output.tail_rows(text_width(width), limit);
    rows.max(1) + usize::from(search)
}

/// `↑↓/jk scroll  n/N next/previous  s stop  / search  Esc back`, adjusted to the search's state.
pub fn hints(app: &App, view: &OutputView, scrolls: bool) -> Vec<Hint> {
    let search = &view.search;
    let mut list = Vec::new();
    if scrolls {
        list.push((key::NAV, action::SCROLL));
    }
    if !view.find.query().is_empty() {
        list.push(match search.focused {
            true => (key::ENTER, action::KEEP),
            false => (key::N_SHIFT_N, action::NEXT_PREVIOUS),
        });
    }
    if crate::tasks_panel::shown_index(app).is_some() {
        list.push(("s", action::STOP));
    }
    list.push(hints::SEARCH);
    // With no task left, Esc closes the window instead of going back to an empty list.
    list.push(match app.session.background_tasks.is_empty() {
        true => hints::CLOSE,
        false => hints::BACK,
    });
    crate::search_field::hints(search.focused, !search.query.is_empty(), &list)
}

/// The framed window: the rows from the scroll top, a scrollbar when they overflow, and the search row under them while searching.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect, content: Rect) {
    let Some(view) = app.tasks_panel.shown_mut() else {
        return;
    };
    let text = text_area(view, content);
    // A rewrap renumbers the rows a selection holds.
    let rewrapped = view.output.wrap(text.width);
    let (rows, searching) = (view.output.rows().len().max(1), searching(view));
    if rewrapped {
        crate::selection::clear_region(app, RegionId::RailWindow);
    }
    let rail = &mut app.rail;
    rail.scroll_max = rows.saturating_sub(text.height.into());
    rail.scroll_page = text.height.into();
    rail.scroll_top = match rail.scroll {
        crate::rail::Scroll::Row(row) => row.min(rail.scroll_max),
        _ => rail.scroll_max,
    };
    let (top, scrolls) = (rail.scroll_top, rail.scroll_max > 0);
    let title = super::tasks_window::title(app, area.width);
    if let Some(view) = app.tasks_panel.shown() {
        super::rail_window::frame(f, area, title, &hints(app, view, scrolls));
    }
    // Only the text selects, never the search row or the scrollbar gutter; a selection holds its rows as they scroll.
    app.view.rail_window_selection_region = crate::selection::Region {
        top: i32::from(text.y) - i32::try_from(top).unwrap_or(i32::MAX),
        scroll_target: crate::selection::ScrollTarget::Output,
        scroll_area: text,
        // Its copy is rebuilt from the whole output, so only on release, not every frame.
        document: true,
        ..super::selection::frame_region(text)
    };
    if let Some(view) = app.tasks_panel.shown() {
        for (index, line) in lines(view, top, text.height).into_iter().enumerate() {
            let y = text.y + index as u16;
            f.buffer_mut().set_line(text.x, y, &line, text.width);
        }
    }
    if rows > usize::from(text.height) {
        let bar = Rect {
            x: content.right() - 1,
            width: 1,
            ..text
        };
        let target = crate::mouse::MouseTarget::RailWindow;
        super::scrollbar::draw_large(app, f, target, bar, rows, text.height.into(), top);
    }
    let row = Rect {
        y: text.bottom(),
        height: 1,
        ..content
    };
    draw_search(app, f, searching.then_some(row));
}

/// `/  query` with the current match's place at its right: `3/12`, `-/12` once it scrolled away, or `no match`; a hidden row leaves no field for a click.
fn draw_search(app: &mut App, f: &mut Frame, row: Option<Rect>) {
    let cursor_on = app.view.cursor_on;
    let Some(view) = app.tasks_panel.shown_mut() else {
        return;
    };
    let Some(row) = row else {
        view.search.area = Rect::default();
        return;
    };
    let place = match (view.find.query().is_empty(), view.find.position()) {
        (true, _) => String::new(),
        (false, (_, 0)) => "no match".to_owned(),
        (false, (Some(at), of)) => format!("{at}/{of}"),
        (false, (None, of)) => format!("-/{of}"),
    };
    let place_width = place.chars().count() as u16;
    let field_row = Rect {
        width: row.width.saturating_sub(place_width + 1),
        ..row
    };
    let bg = theme::background();
    let search = &view.search;
    let field = super::search_field::draw_row(f, field_row, search, "Search output", cursor_on, bg);
    view.search.area = field;
    let x = row.right().saturating_sub(place_width);
    f.buffer_mut()
        .set_string(x, row.y, place, theme::muted_style());
}

/// The `height` rows from `top`, or a muted line saying why there is none.
fn lines(view: &OutputView, top: usize, height: u16) -> Vec<Line<'static>> {
    let output = &view.output;
    let empty = if output.unavailable {
        "Output unavailable"
    } else if !output.loaded {
        "Loading output…"
    } else {
        "No output yet"
    };
    if output.lines().is_empty() {
        return vec![Line::styled(empty, theme::muted_style())];
    }
    let accent = theme::text(theme::text_accent()).add_modifier(Modifier::BOLD);
    let styles = Styles {
        text: theme::text(theme::foreground()),
        hit: accent,
        current: accent.add_modifier(Modifier::REVERSED),
    };
    let found = Found {
        hits: view.find.hits(),
        length: view.find.query().len(),
        current: view.find.current,
    };
    let rows = output.rows();
    let shown = rows.iter().skip(top).take(height.into());
    shown
        .map(|row| lit(output.text(row), row, &found, styles))
        .collect()
}

/// Rows `first..=last` painted at `area`'s columns from y zero, and the folds that rejoin a wrapped line, for copying a selection that runs off screen.
pub fn selection_document(
    app: &App,
    area: Rect,
    first: usize,
    last: usize,
) -> Option<(Buffer, Folds)> {
    let output = &app.tasks_panel.shown()?.output;
    let rows = (output.width() == Some(area.width)).then(|| output.rows())?;
    let last = last.min(rows.len().checked_sub(1)?);
    let height = u16::try_from(last.checked_sub(first)? + 1).ok()?;
    let mut buffer = Buffer::empty(Rect::new(area.x, 0, area.width, height));
    let mut folds = Folds {
        x: area.x,
        rows: Vec::new(),
    };
    for index in first..=last {
        let (row, y) = (&rows[index], (index - first) as u16);
        buffer.set_string(area.x, y, output.text(row), Style::default());
        // A row that goes on with the line above rejoins it, with the spaces the wrap dropped.
        let above = index.checked_sub(1).filter(|above| *above >= first);
        if let Some(above) = above
            .map(|above| &rows[above])
            .filter(|above| above.line == row.line)
        {
            let gap = row.range.start.saturating_sub(above.range.end);
            let gap = u16::try_from(gap).unwrap_or(0);
            let fold = Fold {
                gap,
                hang: 0,
                breaks: false,
            };
            folds.rows.push((y, fold));
        }
    }
    Some((buffer, folds))
}

#[derive(Clone, Copy)]
struct Styles {
    text: Style,
    hit: Style,
    current: Style,
}

/// The search's matches, each `length` bytes long, and the one the view jumped to.
struct Found<'a> {
    hits: &'a [Hit],
    length: usize,
    current: Option<Hit>,
}

/// `text`, the part of its line `row` shows, with the matches it shows lit and the current one marked.
fn lit(text: &str, row: &Row, found: &Found, styles: Styles) -> Line<'static> {
    let (line, offset, end) = (row.line, row.range.start, row.range.end);
    let (hits, length) = (found.hits, found.length);
    // Matches of this line that may reach this row, a match the row above starts included.
    let first = hits.partition_point(|hit| *hit < (line, offset.saturating_sub(length)));
    let mut spans = Vec::new();
    let mut at = 0;
    for hit in hits[first..].iter().take_while(|hit| **hit < (line, end)) {
        let start = hit.1.max(offset) - offset;
        let stop = (hit.1 + length).min(end).saturating_sub(offset);
        if start >= stop
            || start < at
            || !text.is_char_boundary(start)
            || !text.is_char_boundary(stop)
        {
            continue;
        }
        let style = match found.current == Some(*hit) {
            true => styles.current,
            false => styles.hit,
        };
        spans.push(Span::styled(text[at..start].to_owned(), styles.text));
        spans.push(Span::styled(text[start..stop].to_owned(), style));
        at = stop;
    }
    spans.push(Span::styled(text[at..].to_owned(), styles.text));
    Line::from(spans)
}
