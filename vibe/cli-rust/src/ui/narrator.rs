//! The narrator status row: an animated `summarizing` marker in the loading
//! area (Python `NarratorStatus`).

use std::time::Duration;

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::theme;
use crate::app::App;
use crate::turn_summary::NarratorState;

/// Python `SHRINK_FRAMES`: the glyph shrinks while the summary is in flight.
pub const SHRINK_FRAMES: [char; 8] = ['█', '▇', '▆', '▅', '▄', '▃', '▂', '▁'];
/// Python `ANIMATION_INTERVAL`.
pub const ANIMATION_INTERVAL: Duration = Duration::from_millis(150);

/// The row after its animated glyph: `summarizing {Esc/Ctrl+C} to stop`. Every
/// glyph frame is one cell wide, so the row width never changes.
const HINT: &str = " summarizing Esc/Ctrl+C to stop";

/// The row's width, so the loading-area split can reserve it (Python sizes
/// the `NarratorStatus` widget by its content).
pub fn width(app: &App) -> u16 {
    match app.narrator.state {
        NarratorState::Idle => 0,
        NarratorState::Summarizing => 1 + HINT.len() as u16,
    }
}

pub fn draw(app: &App, f: &mut Frame, area: Rect) {
    if area.width == 0 || area.height < 2 || app.narrator.state != NarratorState::Summarizing {
        return;
    }
    // Same top padding as the loading spinner (Python `#loading-area`).
    let content = Rect {
        y: area.y + 1,
        height: 1,
        ..area
    };
    let glyph = SHRINK_FRAMES[app.narrator.frame % SHRINK_FRAMES.len()];
    let key = Style::default()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    let spans = vec![
        Span::styled(
            glyph.to_string(),
            Style::default()
                .fg(theme::ORANGE)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(" summarizing "),
        Span::styled("Esc/Ctrl+C", key),
        Span::styled(" to stop", Style::default().add_modifier(Modifier::DIM)),
    ];
    f.render_widget(Line::from(spans), content);
}
