//! The docked right plan panel Cmd/Alt+\ toggles from the prompt.

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Padding, Paragraph};
use ratatui::Frame;

use super::super::mouse::{self, MouseTarget};
use super::theme;
use crate::app::App;
use crate::hints;
use crate::todo_tracker::{self, TodoItem, TodoStatus};

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

/// The todo sidebar: progress title, items by status, closing hint.
pub fn draw_sidebar(app: &mut App, f: &mut Frame, area: Rect) {
    if area.width < 6 || area.height < 5 {
        return;
    }
    mouse::register_region(app, area, MouseTarget::TodoSidebar);
    let todos = app.todo_tracker.todos().to_vec();
    let title = format!(" Todos · {} done ", todo_tracker::progress_label(&todos));
    let block = Block::default()
        .borders(Borders::ALL)
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
    let help = super::hint_line::lines(&[hints::CLOSE], inner.width);
    let list_area = Rect {
        height: inner.height.saturating_sub(help.len() as u16),
        ..inner
    };
    let hint_area = Rect {
        y: list_area.bottom(),
        height: inner.bottom() - list_area.bottom(),
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
        Paragraph::new(help).alignment(ratatui::layout::Alignment::Right),
        hint_area,
    );
}

/// Sidebar items grouped in order: in-progress, pending, completed, cancelled.
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
        rows.extend(item_rows(todo, width));
        if index + 1 < ordered.len() {
            rows.push(Line::default());
        }
    }
    rows
}

/// `icon text` wrapped to `width`, continuation rows indented under the text.
pub(super) fn item_rows(todo: &TodoItem, width: usize) -> Vec<Line<'static>> {
    let icon = todo_tracker::status_icon(todo.status);
    let style = status_style(todo.status);
    let wrapped = crate::utils::text::wrap_hard(&todo.content, width.saturating_sub(2));
    if wrapped.is_empty() {
        return vec![Line::from(Span::styled(format!("{icon} "), style))];
    }
    wrapped
        .into_iter()
        .enumerate()
        .map(|(line_index, line)| {
            let text = match line_index {
                0 => format!("{icon} {line}"),
                _ => format!("  {line}"),
            };
            Line::from(Span::styled(text, style))
        })
        .collect()
}

/// Active warns, done succeeds, cancelled mutes.
fn status_style(status: TodoStatus) -> Style {
    match status {
        TodoStatus::InProgress => theme::text(theme::warning()),
        TodoStatus::Pending => theme::text(theme::foreground()),
        TodoStatus::Completed => theme::text(theme::success()),
        TodoStatus::Cancelled => theme::muted_style(),
    }
}
