//! The narrator status row in the loading area (Python `NarratorStatus`).

use std::time::Duration;

use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::Frame;

use super::theme;
use crate::app::App;
use crate::turn_summary::NarratorState;

/// Python `SHRINK_FRAMES`: the glyph shrinks until the clip starts playing.
pub const SHRINK_FRAMES: [char; 8] = ['█', '▇', '▆', '▅', '▄', '▃', '▂', '▁'];
/// Python `BAR_FRAMES`: equalizer bars while the summary is spoken.
pub const BAR_FRAMES: [&str; 6] = ["▂▅▇", "▃▆▅", "▅▃▇", "▇▂▅", "▅▇▃", "▃▅▆"];
/// Python `ANIMATION_INTERVAL`.
pub const ANIMATION_INTERVAL: Duration = Duration::from_millis(150);

const KEY: &str = crate::hints::key::ESC_CTRL_C;
const STOP: &str = " to stop";

/// The animated marker and its label; all frames of a state share one width.
fn marker(app: &App) -> Option<(String, &'static str)> {
    let frame = app.narrator.frame;
    match app.narrator.state {
        NarratorState::Idle => None,
        NarratorState::Summarizing => Some((
            SHRINK_FRAMES[frame % SHRINK_FRAMES.len()].to_string(),
            " summarizing ",
        )),
        NarratorState::Speaking => Some((
            BAR_FRAMES[frame % BAR_FRAMES.len()].to_owned(),
            " speaking ",
        )),
    }
}

/// The row's width, so the loading-area split can reserve it (Python sizes
/// the `NarratorStatus` widget by its content).
pub fn width(app: &App) -> u16 {
    marker(app).map_or(0, |(glyph, label)| {
        (glyph.chars().count() + label.len() + KEY.len() + STOP.len()) as u16
    })
}

pub fn draw(app: &App, f: &mut Frame, area: Rect) {
    if area.width == 0 || area.height < 2 {
        return;
    }
    let Some((glyph, label)) = marker(app) else {
        return;
    };
    // Same top padding as the loading spinner (Python `#loading-area`).
    let content = Rect {
        y: area.y + 1,
        height: 1,
        ..area
    };
    let key = Style::default()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    let spans = vec![
        Span::styled(
            glyph,
            Style::default()
                .fg(theme::ORANGE)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(label),
        Span::styled(KEY, key),
        Span::styled(STOP, Style::default().add_modifier(Modifier::DIM)),
    ];
    f.render_widget(Line::from(spans), content);
}
