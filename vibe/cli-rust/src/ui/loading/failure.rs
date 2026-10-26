//! The failed-startup message, wrapped so its closing hint stays visible.

use ratatui::layout::Rect;
use ratatui::style::Style;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Paragraph, Wrap};
use ratatui::Frame;

use super::theme;
use crate::app::{App, Status};

/// The startup-error message. A crashed app server lands in `Failed` too, but
/// its transcript notice explains the failure, so this stays out of the way.
fn message(app: &App) -> Option<Paragraph<'static>> {
    if app.session.status != Status::Failed || app.server_closed {
        return None;
    }
    let message = app
        .session
        .startup_error
        .as_deref()
        .unwrap_or("unknown error");
    let line = Line::from(Span::styled(
        format!(" Failed to initialize: {message}"),
        Style::default().fg(theme::error()),
    ));
    Some(Paragraph::new(line).wrap(Wrap { trim: false }))
}

/// Rows the startup-error message wraps to at `width` (0 when there is none).
pub fn rows(app: &App, width: u16) -> u16 {
    message(app).map_or(0, |message| {
        u16::try_from(message.line_count(width)).unwrap_or(u16::MAX)
    })
}

/// Paint the message bottom-aligned under the loading area's top padding
/// row; false when startup did not fail. `area` is at least two rows high.
pub fn draw(app: &App, f: &mut Frame, area: Rect) -> bool {
    let Some(message) = message(app) else {
        return false;
    };
    let rows = u16::try_from(message.line_count(area.width))
        .unwrap_or(u16::MAX)
        .min(area.height - 1);
    let message_area = Rect {
        y: area.y + area.height - rows,
        height: rows,
        ..area
    };
    f.render_widget(message, message_area);
    true
}
