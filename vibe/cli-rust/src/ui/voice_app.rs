//! Voice settings in the bottom-app slot, matching Python's VoiceApp.

use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::text::Line;
use ratatui::widgets::{Block, Borders, Padding, Paragraph, Wrap};
use ratatui::Frame;

use super::{list_cursor, theme};
use crate::app::App;
use crate::hints::{self, action, key};
use crate::voice_app::LABELS;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let paragraph = Paragraph::new(lines(app, area.width.saturating_sub(4)))
        .style(theme::screen_style())
        .wrap(Wrap { trim: false });
    let height = paragraph.line_count(area.width.saturating_sub(4).max(1)) as u16 + 2;
    app.view.input_area = Rect::default();
    let kind = super::bottom_app::Kind::Voice;
    super::bottom_app::draw(app, f, area, height, kind, |_, f, area| {
        f.render_widget(
            paragraph.block(
                Block::default()
                    .borders(Borders::ALL)
                    .border_style(theme::muted_style())
                    .padding(Padding::horizontal(1)),
            ),
            area,
        );
    });
}

fn lines(app: &App, width: u16) -> Vec<Line<'static>> {
    let title = theme::screen_style()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    let mut lines = vec![Line::styled("Voice Settings", title), Line::default()];
    for (i, label) in LABELS.iter().enumerate() {
        let value = if app.voice_app.values[i] { "On" } else { "Off" };
        let text = format!("  {label}: {value}");
        lines.push(match i == app.voice_app.selected {
            true => Line::styled(
                list_cursor::padded(&text, usize::from(width)),
                list_cursor::style(),
            ),
            false => Line::from(text),
        });
    }
    lines.push(Line::default());
    lines.push(super::hint_line::line(&[
        hints::NAVIGATE,
        (key::SPACE_ENTER, action::TOGGLE),
        (key::ESC, action::EXIT),
    ]));
    lines
}
