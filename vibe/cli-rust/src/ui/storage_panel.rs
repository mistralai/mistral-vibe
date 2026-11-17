//! `/storage` panel view: the summary over a scrolling session list, the review, the confirmation.

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::widgets::{Block, Borders, Clear};
use ratatui::Frame;

use super::storage_rows::{self as rows, Runs};
use super::{list_cursor, theme};
use crate::app::App;
use crate::hints::{self, action, key, Hint};
use crate::storage_panel::model;
use crate::storage_panel::{actions, has_suggestion, View};
use crate::utils::now_ms;

/// One drawn line and the option it belongs to, `None` for text.
struct Line {
    option: Option<usize>,
    highlighted: bool,
    runs: Runs,
}

impl Line {
    fn text(runs: Runs) -> Self {
        Self {
            option: None,
            highlighted: false,
            runs,
        }
    }
}

/// Columns the box loses to its border, padding and the option bar's gutter.
const GUTTER: u16 = 6;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let width = area.width.saturating_sub(GUTTER) as usize;
    let (top, list, bottom) = lines(app, width);
    let chrome = top.len() as u16 + below(&bottom) + 2;
    let max_list = (area.height * 3 / 5).saturating_sub(chrome).max(3);
    let shown = (list.len() as u16).min(max_list);
    let height = chrome + shown;
    let kind = super::bottom_app::Kind::Storage;
    super::bottom_app::draw(app, f, area, height, kind, |app, f, area| {
        draw_box(app, f, area, &top, &list, &bottom)
    });
}

fn draw_box(
    app: &mut App,
    f: &mut Frame,
    area: Rect,
    top: &[Line],
    list: &[Line],
    bottom: &[Line],
) {
    f.render_widget(Clear, area);
    let bg = theme::background();
    f.buffer_mut().set_style(area, Style::default().bg(bg));
    let border = theme::border_style().bg(bg);
    f.render_widget(
        Block::default().borders(Borders::ALL).border_style(border),
        area,
    );
    let inner = Rect::new(
        area.x + 2,
        area.y + 1,
        area.width.saturating_sub(4),
        area.height.saturating_sub(2),
    );
    let mut y = inner.y;
    for line in top {
        draw_line(f, inner, y, line);
        y += 1;
    }
    let visible = inner
        .height
        .saturating_sub(top.len() as u16 + below(bottom));
    let list_area = Rect::new(inner.x, y, inner.width, visible);
    let options: Vec<Option<usize>> = list.iter().map(|line| line.option).collect();
    let scroll = match app.storage.view {
        View::Review => app.storage.review.list.layout(&options, list_area),
        _ => app.storage.list.layout(&options, list_area),
    };
    for line in list.iter().skip(scroll).take(visible as usize) {
        draw_line(f, inner, y, line);
        y += 1;
    }
    y = inner.bottom().saturating_sub(below(bottom) - 1);
    for line in bottom {
        draw_line(f, inner, y, line);
        y += 1;
    }
    super::hint_line::draw_clipped(f, inner.x, inner.bottom() - 1, inner.width, &footer(app));
}

/// Rows under the list: a blank, the bottom lines and a blank when there are any, the footer.
fn below(bottom: &[Line]) -> u16 {
    match bottom.len() as u16 {
        0 => 2,
        lines => lines + 3,
    }
}

fn draw_line(f: &mut Frame, inner: Rect, y: u16, line: &Line) {
    let bar = Rect::new(inner.x, y, inner.width, 1);
    if line.highlighted {
        list_cursor::paint(f, bar);
    }
    let indent = if line.option.is_some() { 2 } else { 0 };
    let mut x = bar.x + indent;
    for (text, style) in &line.runs {
        let room = bar.right().saturating_sub(x) as usize;
        x = f.buffer_mut().set_stringn(x, y, text, room, *style).0;
    }
}

/// The header lines, the scrolling list, and the lines under it.
fn lines(app: &App, width: usize) -> (Vec<Line>, Vec<Line>, Vec<Line>) {
    let state = &app.storage;
    let now = now_ms();
    let Some(snapshot) = state.usage.snapshot.as_ref() else {
        let top = vec![
            Line::text(rows::header(&state.usage, state.refreshing, now)),
            Line::text(Vec::new()),
        ];
        let list = [
            "Measuring Vibe storage…",
            "This can take a few minutes with many sessions and worktrees.",
        ];
        return (
            top,
            list.into_iter().map(|t| Line::text(rows::dim(t))).collect(),
            Vec::new(),
        );
    };
    let title_width = width.saturating_sub(7 + 10 + 2 + 19 + 2 + 20).clamp(12, 44);
    let current = app.session.session_id.as_deref();
    let all = model::rows(snapshot, current);
    let mut bottom = Vec::new();
    if let Some(busy) = &state.busy {
        bottom.push(Line::text(rows::dim(busy)));
    } else if let Some(confirm) = &state.confirm {
        let question = actions::question(confirm);
        for line in crate::utils::text::wrap_hard(&question, width + 2) {
            bottom.push(Line::text(rows::dim(&line)));
        }
    }
    if state.view == View::Review {
        let review = &state.review;
        let top = vec![
            Line::text(rows::header(&state.usage, state.refreshing, now).into_iter().take(1).map(|(_, s)| ("Archive inactive sessions".to_owned(), s)).collect()),
            Line::text(rows::dim("No activity for 30+ days. Worktrees are removed once no other session uses them; history is kept.")),
            Line::text(Vec::new()),
        ];
        let list = review
            .order
            .iter()
            .enumerate()
            .filter_map(|(index, id)| {
                let row = all.iter().find(|row| row.session.session_id == *id)?;
                let highlighted = index == review.list.selected();
                let runs = rows::review(
                    row,
                    review.selected.contains(id),
                    highlighted,
                    now,
                    title_width.saturating_sub(4),
                );
                Some(Line {
                    option: Some(index),
                    highlighted,
                    runs,
                })
            })
            .collect();
        let estimate = model::estimate(snapshot, &review.selected);
        bottom.insert(
            0,
            Line::text(rows::estimate(review.selected.len(), estimate)),
        );
        return (top, list, bottom);
    }
    let mut top = vec![
        Line::text(rows::header(&state.usage, state.refreshing, now)),
        Line::text(rows::categories(snapshot)),
        Line::text(Vec::new()),
    ];
    if state.usage.disk.is_low {
        top.push(Line::text(rows::low_disk()));
    }
    let selected = state.list.selected();
    let offset = usize::from(has_suggestion(&state.usage));
    let mut list = Vec::new();
    if offset == 1 {
        let highlighted = selected == 0;
        list.push(Line {
            option: Some(0),
            highlighted,
            runs: rows::suggestion(snapshot, highlighted),
        });
        list.push(Line::text(Vec::new()));
    }
    if all.is_empty() {
        list.push(Line::text(rows::dim("No saved sessions.")));
    }
    for (index, row) in all.iter().enumerate() {
        let highlighted = selected == index + offset;
        list.push(Line {
            option: Some(index + offset),
            highlighted,
            runs: rows::session(row, highlighted, now, title_width),
        });
        if highlighted && state.view == View::Details {
            let mut runs = vec![(" ".repeat(11), Style::default())];
            runs.extend(rows::details(row));
            list.push(Line {
                option: Some(index + offset),
                highlighted: false,
                runs,
            });
        }
    }
    (top, list, bottom)
}

fn footer(app: &App) -> Vec<Hint> {
    let state = &app.storage;
    if state.busy.is_some() {
        return match crate::storage_panel::waiting_for_scan(app) {
            true => vec![hints::CLOSE],
            false => Vec::new(),
        };
    }
    if state.confirm.is_some() {
        return vec![
            (key::YES_ENTER, action::CONFIRM),
            (key::NO_ESC, action::CANCEL),
        ];
    }
    match state.view {
        View::Review => vec![
            hints::NAVIGATE,
            (key::SPACE, action::TOGGLE),
            ("a", action::ALL),
            (
                "s",
                if state.review.by_date {
                    action::SORT_DATE
                } else {
                    action::SORT_SIZE
                },
            ),
            (key::ENTER, action::ARCHIVE),
            hints::BACK,
        ],
        View::Details => vec![
            ("a", archive_action(app)),
            ("d", action::DELETE),
            ("r", action::REFRESH),
            hints::BACK,
        ],
        View::Panel
            if matches!(
                crate::storage_panel::target(app),
                Some(crate::storage_panel::Target::Suggestion)
            ) =>
        {
            vec![
                hints::NAVIGATE,
                (key::ENTER, action::REVIEW),
                ("r", action::REFRESH),
                hints::CLOSE,
            ]
        }
        View::Panel => vec![
            hints::NAVIGATE,
            (key::ENTER, action::DETAILS),
            ("a", archive_action(app)),
            ("d", action::DELETE),
            ("r", action::REFRESH),
            hints::CLOSE,
        ],
    }
}

/// `a` unarchives an archived session, like Desktop's Unarchive.
fn archive_action(app: &App) -> &'static str {
    match crate::storage_panel::target(app) {
        Some(crate::storage_panel::Target::Session(row)) if row.session.archived => {
            action::UNARCHIVE
        }
        _ => action::ARCHIVE,
    }
}
