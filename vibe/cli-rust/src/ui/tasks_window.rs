//! The background tasks window: the running tasks, or one task's output.

use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::{list_cursor, task_output, theme};
use crate::app::App;
use crate::hints::{self, action, key};
use crate::utils::text::ellipsize;

/// Rows the content needs at `width`, up to `cap`: one per task, or the output's search and last lines.
pub fn content_rows(app: &App, width: u16, cap: u16) -> u16 {
    let rows = match app.tasks_panel.shown() {
        Some(view) => task_output::content_rows(view, width, cap),
        None => app.session.background_tasks.len(),
    };
    u16::try_from(rows).unwrap_or(u16::MAX)
}

/// `Background tasks 2`, or the shown task's bold command, cut to keep whether it still runs in `width`.
pub fn title(app: &App, width: u16) -> Vec<Span<'static>> {
    let bold = theme::text(theme::foreground()).add_modifier(Modifier::BOLD);
    let (head, detail) = match app.tasks_panel.shown() {
        Some(view) => {
            let running = crate::tasks_panel::shown_index(app).is_some();
            let detail = if running { "running" } else { "ended" };
            let room = usize::from(width).saturating_sub(detail.len() + 7);
            (ellipsize(&view.output.command, room), detail.to_owned())
        }
        None => (
            "Background tasks".to_owned(),
            app.session.background_tasks.len().to_string(),
        ),
    };
    vec![
        Span::styled(head, bold),
        Span::styled(format!(" {detail}"), theme::muted_style()),
    ]
}

/// The framed window with the task list in `content`, or the output in its place.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect, content: Rect) {
    if app.tasks_panel.shown().is_some() {
        return task_output::draw(app, f, area, content);
    }
    app.rail.scroll_max = 0;
    let list_hints = [
        hints::NAVIGATE,
        (key::ENTER, action::VIEW),
        ("s", action::STOP),
        hints::CLOSE,
    ];
    super::rail_window::frame(f, area, title(app, area.width), &list_hints);
    list(app, f, content);
}

/// One `▪ command` row per running task, the highlighted one on the list-cursor bar, a scrollbar on overflow.
fn list(app: &mut App, f: &mut Frame, content: Rect) {
    let rows = app.session.background_tasks.len();
    let scrolls = rows > usize::from(content.height);
    let options = Rect {
        width: content.width.saturating_sub(if scrolls { 2 } else { 0 }),
        ..content
    };
    let offset = app.tasks_panel.list.layout_rows(rows, options);
    let target = crate::mouse::MouseTarget::TasksPanel;
    crate::mouse::register_region(app, content, target);
    for (row, index) in (offset..rows).take(options.height.into()).enumerate() {
        let row = row as u16;
        let highlighted = index == app.tasks_panel.list.selected();
        let line_area = Rect {
            y: options.y + row,
            height: 1,
            ..options
        };
        if highlighted {
            list_cursor::paint(f, line_area);
        }
        let (text, _) = list_cursor::styles(highlighted);
        let glyph = match highlighted {
            true => text,
            false => theme::text(theme::text_accent()),
        };
        let command = app.session.background_tasks[index].command.clone();
        let line = Line::from(vec![Span::styled("▪ ", glyph), Span::styled(command, text)]);
        f.buffer_mut()
            .set_line(line_area.x, line_area.y, &line, line_area.width);
    }
    if scrolls {
        let bar = Rect {
            x: content.right() - 1,
            width: 1,
            ..content
        };
        let (total, visible) = (rows as u16, options.height);
        super::scrollbar::draw(app, f, target, bar, total, visible, offset as u16);
    }
}
