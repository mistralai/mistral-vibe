//! The window a rail item opens in the input box's place under the rail: one frame, a title on the top border, rows, and hints on the bottom border.

use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders};
use ratatui::Frame;

use super::{
    artifacts_modal, goal_panel, goal_row, hint_line, subagent_list, tasks_window, theme, todo,
};
use crate::app::App;
use crate::goal::format;
use crate::hints::{self, action, key, Hint};
use crate::rail::{self, Item, Scroll};
use crate::todo_tracker::TodoStatus;

/// Content rows a window shows at most before it clips.
const MAX_ROWS: u16 = 10;

/// The open window, unless it is a subagent list with no rows to show; the loop manager draws as its own box.
fn shown(app: &App) -> Option<Item> {
    rail::open_item(app).filter(|item| match item {
        Item::Loops => false,
        Item::Agents => subagent_list::height(app) > 0,
        _ => true,
    })
}

/// The window's rows in the box's slot: at least `box_height`, so opening one never moves the rail.
pub fn slot_height(app: &App, screen: Rect, box_height: u16) -> u16 {
    match height(app, screen) {
        0 => 0,
        rows => rows.max(box_height),
    }
}

/// Rows the window takes on `screen`, borders included; 0 when none is open.
fn height(app: &App, screen: Rect) -> u16 {
    let Some(item) = shown(app) else {
        return 0;
    };
    let cap = MAX_ROWS.min((screen.height / 2).saturating_sub(2));
    match content_rows(app, item, screen.width.saturating_sub(4), cap).min(cap) {
        0 => 0,
        rows => rows + 2,
    }
}

/// Rows the window's content needs at `width` columns, up to `cap`, the goal's objective and todos wrapped.
fn content_rows(app: &App, item: Item, width: u16, cap: u16) -> u16 {
    match item {
        // `shown` never yields the loop manager, which draws as its own box.
        Item::Loops => 0,
        Item::Agents => subagent_list::height(app),
        Item::Tasks => tasks_window::content_rows(app, width, cap),
        Item::Prs => crate::artifacts_modal::option_count(app) as u16,
        Item::Goal => goal_lines(app, width).len() as u16,
        Item::Plan => plan_lines(app, width).len() as u16,
    }
}

/// Paint the open window into `area`; the subagent list always draws so it can reset its hit map.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let item = shown(app).filter(|_| area.height > 2);
    let content = Rect {
        x: area.x + 2,
        y: area.y + 1,
        width: area.width.saturating_sub(4),
        height: area.height.saturating_sub(2),
    };
    if item != Some(Item::Agents) {
        subagent_list::draw(app, f, Rect::default());
    }
    let Some(item) = item else {
        // A window that closed, or whose item left the rail, takes its selection with it.
        crate::selection::clear_region(app, crate::selection::RegionId::RailWindow);
        return;
    };
    crate::mouse::register_region(app, area, crate::mouse::MouseTarget::RailWindow);
    app.view.rail_window_selection_region = super::selection::frame_region(content);
    if item == Item::Tasks {
        tasks_window::draw(app, f, area, content);
        return super::selection::overlay_region(app, f, crate::selection::RegionId::RailWindow);
    }
    let text = match item {
        Item::Goal => goal_lines(app, content.width),
        Item::Plan => plan_lines(app, content.width),
        _ => Vec::new(),
    };
    app.rail.scroll_max = text.len().saturating_sub(content.height.into());
    let scrolls = app.rail.scroll_max > 0;
    frame(f, area, title(app, item), &hints(app, item, scrolls));
    match item {
        Item::Agents => {
            let list = Rect {
                x: area.x + 1,
                width: area.width.saturating_sub(2),
                ..content
            };
            subagent_list::draw(app, f, list);
        }
        Item::Prs => artifacts_modal::draw_rows(app, f, content),
        Item::Loops | Item::Tasks => {}
        Item::Goal | Item::Plan => {
            let top = match app.rail.scroll {
                Scroll::Current => current_row(app, content.width),
                Scroll::End => app.rail.scroll_max,
                Scroll::Row(row) => row,
            };
            app.rail.scroll_top = top.min(app.rail.scroll_max);
            lines(f, content, text, app.rail.scroll_top);
        }
    }
    super::selection::overlay_region(app, f, crate::selection::RegionId::RailWindow);
}

/// The border, `─ title ─` on the left of the top edge, the hints that fit on the right of the bottom edge.
pub(super) fn frame(f: &mut Frame, area: Rect, title: Vec<Span<'static>>, hints: &[Hint]) {
    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(theme::border_style());
    f.render_widget(block, area);
    let mut spans = vec![Span::raw(" ")];
    spans.extend(title);
    spans.push(Span::raw(" "));
    let buffer = f.buffer_mut();
    buffer.set_line(
        area.x + 2,
        area.y,
        &Line::from(spans),
        area.width.saturating_sub(4),
    );
    let mut hints = hints;
    while hints.len() > 1 && hints::width(hints) + 5 > area.width {
        hints = &hints[1..];
    }
    let mut hint = vec![Span::raw(" ")];
    hint.extend(hint_line::spans(hints));
    hint.push(Span::raw(" "));
    let width: u16 = hint.iter().map(|span| span.width() as u16).sum();
    let width = width.min(area.width.saturating_sub(4));
    let x = area.right().saturating_sub(width + 3).max(area.x + 1);
    buffer.set_line(x, area.bottom() - 1, &Line::from(hint), width);
}

/// A bold name, then its muted detail.
fn title(app: &App, item: Item) -> Vec<Span<'static>> {
    let bold = theme::text(theme::foreground()).add_modifier(Modifier::BOLD);
    let muted = theme::muted_style();
    let (head, detail) = match item {
        // The loop manager has its own box, the tasks window its own title.
        Item::Loops | Item::Tasks => return Vec::new(),
        Item::Agents => match app.subagents.viewed_child() {
            Some(child) => (
                format!("Viewing {}", crate::subagents::display_name(&child.name)),
                "read-only".to_owned(),
            ),
            None => {
                let (running, listed) = rail::agent_counts(app);
                let detail = match running {
                    0 => listed.to_string(),
                    _ => format!("{running} running"),
                };
                ("Subagents".to_owned(), detail)
            }
        },
        Item::Prs => return artifacts_modal::title(app),
        Item::Goal => {
            let Some(goal) = &app.goal.goal else {
                return Vec::new();
            };
            let (glyph, style) = goal_row::icon(goal.status);
            return vec![
                Span::styled(format!("{glyph} "), style),
                Span::styled("Goal", bold),
                Span::styled(format!(" {}", format::status_label(goal.status)), muted),
            ];
        }
        Item::Plan => (
            "Todo".to_owned(),
            crate::todo_tracker::progress_label(app.todo_tracker.todos()),
        ),
    };
    vec![
        Span::styled(head, bold),
        Span::styled(format!(" {detail}"), muted),
    ]
}

fn hints(app: &App, item: Item, scrolls: bool) -> Vec<Hint> {
    let mut hints = match item {
        Item::Agents if app.subagents.viewed_subagent_id.is_some() => vec![hints::BACK],
        Item::Agents => vec![hints::NAVIGATE, (key::ENTER, action::VIEW), hints::CLOSE],
        Item::Prs => artifacts_modal::hints(app),
        Item::Goal => match &app.goal.goal {
            Some(goal) => goal_panel::detail_hints(goal),
            None => vec![hints::CLOSE],
        },
        Item::Loops | Item::Plan | Item::Tasks => vec![hints::CLOSE],
    };
    if scrolls {
        hints.insert(0, (key::NAV, action::SCROLL));
    }
    hints
}

/// The objective wrapped to `width`, then its time and token usage.
fn goal_lines(app: &App, width: u16) -> Vec<Line<'static>> {
    let Some(goal) = &app.goal.goal else {
        return Vec::new();
    };
    let text = theme::text(theme::foreground());
    let mut lines: Vec<Line> = goal_panel::objective_rows(goal, usize::from(width))
        .into_iter()
        .map(|row| Line::styled(row, text))
        .collect();
    let stats = format!(
        "{} · {} tokens",
        format::duration(goal.time_used_seconds),
        format::usage(goal)
    );
    lines.push(Line::styled(stats, theme::muted_style()));
    lines
}

/// The todos in the order the agent wrote them, each wrapped to `width`.
fn plan_lines(app: &App, width: u16) -> Vec<Line<'static>> {
    app.todo_tracker
        .todos()
        .iter()
        .flat_map(|todo| todo::item_rows(todo, usize::from(width)))
        .collect()
}

/// The plan window row where the in-progress todo starts, 0 without one.
fn current_row(app: &App, width: u16) -> usize {
    let todos = app.todo_tracker.todos();
    let Some(current) = todos
        .iter()
        .position(|todo| todo.status == TodoStatus::InProgress)
    else {
        return 0;
    };
    todos[..current]
        .iter()
        .map(|todo| todo::item_rows(todo, usize::from(width)).len())
        .sum()
}

/// Paint `lines` top-down from `scroll`, clipping what does not fit.
fn lines(f: &mut Frame, content: Rect, lines: Vec<Line<'static>>, scroll: usize) {
    let shown = lines.into_iter().skip(scroll).take(content.height.into());
    for (row, line) in shown.enumerate() {
        let y = content.y + row as u16;
        f.buffer_mut().set_line(content.x, y, &line, content.width);
    }
}
