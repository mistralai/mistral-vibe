//! The pinned todo line above the input and the docked right plan panel.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, Padding, Paragraph};
use ratatui::Frame;

use super::super::mouse::{self, MouseTarget};
use super::theme;
use crate::app::App;
use crate::todo_tracker::{self, TodoItem, TodoStatus};

/// Exactly one line: it sits above the input box, which any growth would shove down.
pub fn row_height(app: &App) -> u16 {
    u16::from(!app.todo_tracker.todos().is_empty())
}

/// Below this the docked column would leave the chat too narrow to read.
const MIN_DOCK_WIDTH: u16 = 60;

/// Carve the panel's column off the chat area, or leave the area whole.
pub fn split(app: &App, area: Rect) -> (Rect, Option<Rect>) {
    if !app.todo_sidebar.open || area.width < MIN_DOCK_WIDTH {
        return (area, None);
    }
    let sidebar_width = (area.width / 3).clamp(20, 48);
    let main = Rect {
        width: area.width - sidebar_width,
        ..area
    };
    let sidebar = Rect {
        x: main.right(),
        width: sidebar_width,
        ..area
    };
    (main, Some(sidebar))
}

/// Both modifiers, because `is_todo_key` takes either and only macOS has a Cmd byte to send.
const TOGGLE_HINT: &str = "Cmd/Alt+\\";

/// Python `TodoStatusRow`: one muted summary line, ellipsized, clickable.
pub fn draw_row(app: &mut App, f: &mut Frame, area: Rect) {
    if area.width < 1 || area.height < 1 {
        return;
    }
    mouse::register_region(app, area, MouseTarget::TodoRow);
    let Some(summary) = app.todo_tracker.summary() else {
        return;
    };
    let hint = [
        Span::styled(TOGGLE_HINT, Style::default().fg(theme::primary())),
        Span::styled(" plan", theme::muted_style()),
    ];
    let hint_width: u16 = hint.iter().map(|span| span.width() as u16).sum();
    let text =
        crate::utils::text::ellipsize(&summary, area.width.saturating_sub(hint_width + 1) as usize);
    let hint_x = area.right().saturating_sub(hint_width);
    f.render_widget(
        Paragraph::new(Line::from(Span::styled(text, theme::muted_style()))),
        area,
    );
    f.render_widget(
        Paragraph::new(Line::from(Vec::from(hint))),
        Rect {
            x: hint_x,
            width: hint_width,
            ..area
        },
    );
}

/// Python `TodoOverlayScreen` content: progress title, items by status, closing hint.
pub fn draw_sidebar(app: &mut App, f: &mut Frame, area: Rect) {
    if area.width < 6 || area.height < 5 {
        return;
    }
    mouse::register_region(app, area, MouseTarget::TodoSidebar);
    let todos = app.todo_tracker.todos().to_vec();
    let title = format!(" Todos · {} done ", todo_tracker::progress_label(&todos));
    let block = Block::default()
        .borders(Borders::ALL)
        .border_type(BorderType::Rounded)
        .border_style(theme::text(theme::primary()))
        .style(Style::default().bg(theme::surface()))
        .title(Span::styled(
            title,
            theme::text(theme::primary()).add_modifier(Modifier::BOLD),
        ))
        .padding(Padding::horizontal(1));
    let inner = block.inner(area);
    f.render_widget(block, area);
    if inner.width < 1 || inner.height < 2 {
        return;
    }
    let list_area = Rect {
        height: inner.height - 1,
        ..inner
    };
    let hint_area = Rect {
        y: list_area.bottom(),
        height: 1,
        ..inner
    };
    let rows = sidebar_rows(&todos, list_area.width as usize);
    let max_scroll = rows.len().saturating_sub(list_area.height as usize);
    app.todo_sidebar.scroll = app.todo_sidebar.scroll.min(max_scroll);
    let visible: Vec<Line> = rows
        .into_iter()
        .skip(app.todo_sidebar.scroll)
        .take(list_area.height as usize)
        .collect();
    f.render_widget(Paragraph::new(visible), list_area);
    f.render_widget(
        Paragraph::new(Span::styled("esc to close", theme::muted_style()))
            .alignment(ratatui::layout::Alignment::Right),
        hint_area,
    );
}

/// Python `TodoOverlayScreen._compose_items`: in-progress, pending, completed, cancelled.
fn sidebar_rows(todos: &[TodoItem], width: usize) -> Vec<Line<'static>> {
    if todos.is_empty() {
        return vec![Line::from(Span::styled("No todos", theme::muted_style()))];
    }
    let mut ordered: Vec<&TodoItem> = Vec::new();
    for status in [
        TodoStatus::InProgress,
        TodoStatus::Pending,
        TodoStatus::Completed,
        TodoStatus::Cancelled,
    ] {
        ordered.extend(todos.iter().filter(|todo| todo.status == status));
    }
    let mut rows = Vec::new();
    for (index, todo) in ordered.iter().enumerate() {
        let icon = todo_tracker::status_icon(todo.status);
        let style = status_style(todo.status);
        let wrapped = crate::utils::text::wrap_hard(&todo.content, width.saturating_sub(2));
        if wrapped.is_empty() {
            rows.push(Line::from(Span::styled(format!("{icon} "), style)));
        }
        for (line_index, line) in wrapped.into_iter().enumerate() {
            let text = if line_index == 0 {
                format!("{icon} {line}")
            } else {
                format!("  {line}")
            };
            rows.push(Line::from(Span::styled(text, style)));
        }
        if index + 1 < ordered.len() {
            rows.push(Line::default());
        }
    }
    rows
}

/// Python `.todo-{status}` classes: active warns, done succeeds, cancelled mutes.
fn status_style(status: TodoStatus) -> Style {
    match status {
        TodoStatus::InProgress => theme::text(theme::warning()),
        TodoStatus::Pending => theme::text(theme::foreground()),
        TodoStatus::Completed => theme::text(theme::success()),
        TodoStatus::Cancelled => theme::muted_style(),
    }
}
