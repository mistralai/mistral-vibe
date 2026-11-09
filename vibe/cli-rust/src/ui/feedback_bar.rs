//! The one-line session feedback prompt.

use ratatui::layout::{Alignment, Rect};
use ratatui::text::{Line, Span};
use ratatui::widgets::Paragraph;
use ratatui::Frame;

use super::theme;
use crate::app::App;
use crate::feedback::Message;

pub fn width(app: &App) -> u16 {
    message_line(app)
        .map(|line| u16::try_from(line.width()).unwrap_or(u16::MAX))
        .unwrap_or_default()
}

pub fn draw(app: &App, f: &mut Frame, area: Rect) {
    let Some(line) = message_line(app) else {
        return;
    };
    if area.height == 0 {
        return;
    }
    let row = Rect {
        y: area.y + area.height - 1,
        height: 1,
        ..area
    };
    f.render_widget(
        Paragraph::new(line)
            .style(theme::text(theme::foreground()))
            .alignment(Alignment::Right),
        row,
    );
}

fn message_line(app: &App) -> Option<Line<'static>> {
    Some(match &app.feedback.message {
        Message::Hidden => return None,
        Message::Prompt => prompt(),
        Message::ThankYou => Line::from("Thank you for your feedback!"),
        Message::Snoozed => Line::from(snooze_message(app.feedback.snooze_duration_seconds)),
    })
}

fn snooze_message(seconds: Option<u64>) -> String {
    let Some(seconds) = seconds else {
        return "Snoozed. See you later!".to_owned();
    };
    let (count, unit) = if seconds >= 86_400 && seconds % 86_400 == 0 {
        (seconds / 86_400, "day")
    } else if seconds >= 3_600 && seconds % 3_600 == 0 {
        (seconds / 3_600, "hour")
    } else if seconds >= 60 && seconds % 60 == 0 {
        (seconds / 60, "minute")
    } else {
        (seconds, "second")
    };
    let duration = if count == 1 {
        format!("a {unit}")
    } else {
        format!("{count} {unit}s")
    };
    format!("Snoozed for {duration}. See you later!")
}

fn prompt() -> Line<'static> {
    let key = theme::text(theme::primary());
    Line::from(vec![
        Span::raw("How's your session with Vibe?  "),
        Span::styled("1", key),
        Span::raw(": good  "),
        Span::styled("2", key),
        Span::raw(": fine  "),
        Span::styled("3", key),
        Span::raw(": bad  "),
        Span::styled("0", key),
        Span::raw(": snooze"),
    ])
}
