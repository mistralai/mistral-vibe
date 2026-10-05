//! Voice settings in the bottom-app slot, matching Python's VoiceApp.

use ratatui::layout::Rect;
use ratatui::style::Modifier;
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Padding, Paragraph, Wrap};
use ratatui::Frame;

use super::{bottom_bar, loading, theme, transcript};
use crate::app::App;
use crate::voice_app::LABELS;

pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    f.buffer_mut().set_style(area, theme::screen_style());
    let paragraph = Paragraph::new(lines(app))
        .style(theme::screen_style())
        .wrap(Wrap { trim: false });
    let height = paragraph.line_count(area.width.saturating_sub(4).max(1)) as u16 + 2;
    let loading_height = if app.view.transcript.is_empty() { 3 } else { 2 };
    let chunks = super::bottom_app_chunks(app, area, loading_height, height);
    transcript::draw(app, f, chunks[0]);
    loading::draw(app, f, chunks[1]);
    app.view.input_area = Rect::default();
    crate::mouse::register_region(app, chunks[2], crate::mouse::MouseTarget::Blocked);
    f.render_widget(
        paragraph.block(
            Block::default()
                .borders(Borders::ALL)
                .border_style(theme::muted_style())
                .padding(Padding::horizontal(1)),
        ),
        chunks[2],
    );
    super::todo::draw_row(app, f, chunks[4]);
    bottom_bar::draw(app, f, chunks[3]);
}

fn lines(app: &App) -> Vec<Line<'static>> {
    let title = theme::screen_style()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    let mut lines = vec![Line::styled("Voice Settings", title), Line::default()];
    for (i, label) in LABELS.iter().enumerate() {
        let cursor = if i == app.voice_app.selected {
            "› "
        } else {
            "  "
        };
        let value = if app.voice_app.values[i] { "On" } else { "Off" };
        lines.push(Line::from(format!("{cursor}{label}: {value}")));
    }
    lines.push(Line::default());
    lines.push(Line::from(vec![
        Span::styled("↑↓/jk", title),
        Span::styled(" navigate  ", theme::muted_style()),
        Span::styled("Space/Enter", title),
        Span::styled(" toggle  ", theme::muted_style()),
        Span::styled("Esc", title),
        Span::styled(" exit", theme::muted_style()),
    ]));
    lines
}
