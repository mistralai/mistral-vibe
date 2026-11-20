//! Loading area: a spinner, gradient status label, and key hints.

pub mod failure;
mod hint;
mod idle;
mod line;
mod snake;
mod wrap;

pub(crate) use hint::queue as queue_hints;

use std::time::Duration;

use crate::clock::UiInstant;

use ratatui::layout::Rect;
use ratatui::style::Color;
use ratatui::text::Span;
use ratatui::Frame;

use self::snake::Snake;
use super::theme;
use crate::app::{App, Status};

/// Default label; `reasoning` entries swap it for `THINKING_LOADING_STATUS`.
pub const DEFAULT_LOADING_STATUS: &str = "Generating";
pub const THINKING_LOADING_STATUS: &str = "Thinking";
pub const INITIALIZING_LOADING_STATUS: &str = "Initializing";
/// Label while a turn retries after a transient failure.
pub const RETRYING_LOADING_STATUS: &str = "Retrying";
/// Label for a slash command fetching in the background (`/whoami`).
pub const COMMAND_LOADING_STATUS: &str = "Loading";
/// Label while the `/resume` picker waits for `session/resume`.
pub const RESUMING_LOADING_STATUS: &str = "Resuming";
pub const SHELL_LOADING_STATUS: &str = "Running command";
/// Still glyph for later work: a waiting agent, or an idle session's next loop.
const WAITING_GLYPH: &str = "◷";

/// Spinner glyph + label gradient state, recreated at the start of each turn.
pub struct LoadingAnim {
    snake: Snake,
    /// Current label text (`Generating`/`Thinking`), mirroring `_base_status`.
    label: String,
    /// Blocking callback label, mirroring `_action_required_status`.
    action_required: Option<String>,
    /// Label to restore once the callback is answered.
    label_before_action_required: Option<String>,
    /// Elapsed time already accumulated before the current pause.
    paused_total: Duration,
    /// When the elapsed counter was paused, or `None` while it runs.
    pause_start: Option<UiInstant>,
    /// Index into `theme::LOADING_GRADIENT` of the current base color.
    color_index: usize,
    /// Wipe direction through the palette: +1 up, -1 down. Ping-pongs at the ends.
    direction: i32,
    /// How many leading positions have flipped to the next color this cycle.
    progress: usize,
}

impl Default for LoadingAnim {
    fn default() -> Self {
        Self {
            snake: Snake::default(),
            label: DEFAULT_LOADING_STATUS.to_owned(),
            action_required: None,
            label_before_action_required: None,
            paused_total: Duration::default(),
            pause_start: None,
            color_index: 0,
            direction: 1,
            progress: 0,
        }
    }
}

impl LoadingAnim {
    /// Current label text (`Generating`/`Thinking`/`Retrying`).
    pub fn label(&self) -> &str {
        &self.label
    }

    /// Set the label without disturbing the spinner or gradient.
    pub fn set_label(&mut self, label: &str) {
        if let Some(action_required) = &self.action_required {
            if action_required != label {
                self.label_before_action_required = Some(label.to_owned());
            }
            return;
        }
        self.label = label.to_owned();
    }

    /// Show a blocking user-action label and freeze the elapsed counter.
    pub fn begin_action_required(&mut self, label: &str) {
        if self.action_required.is_none() {
            self.label_before_action_required = Some(self.label.clone());
            self.pause_start = Some(crate::clock::now());
        }
        self.action_required = Some(label.to_owned());
        self.label = label.to_owned();
    }

    /// Resume progress once the callback has been answered.
    pub fn end_action_required(&mut self) {
        if self.action_required.take().is_none() {
            return;
        }
        if let Some(pause_start) = self.pause_start.take() {
            self.paused_total += crate::clock::since(pause_start);
        }
        self.label = self
            .label_before_action_required
            .take()
            .unwrap_or_else(|| DEFAULT_LOADING_STATUS.to_owned());
    }

    /// Turn elapsed time, excluding whatever a blocking callback froze.
    fn elapsed(&self, since: UiInstant) -> Duration {
        let running = match self.pause_start {
            Some(pause_start) => pause_start.duration_since(since),
            None => crate::clock::since(since),
        };
        running.saturating_sub(self.paused_total)
    }

    /// Colored positions: the spinner (0), each label char, then the ellipsis.
    fn total_elements(&self) -> usize {
        1 + self.label.chars().count() + 1
    }

    /// Advance one tick: step the snake and the gradient wipe front.
    pub fn tick(&mut self) {
        self.snake.tick();
        self.progress += 1;
        if self.progress > self.total_elements() {
            self.color_index = (self.color_index as i32 + self.direction) as usize;
            let last = theme::LOADING_GRADIENT.len() - 1;
            if !(0 < self.color_index && self.color_index < last) {
                self.direction = -self.direction;
            }
            self.progress = 0;
        }
    }

    /// The color for a position: before the wipe front shows the next color.
    fn color_at(&self, pos: usize) -> Color {
        let g = theme::LOADING_GRADIENT;
        if pos < self.progress {
            g[(self.color_index as i32 + self.direction) as usize]
        } else {
            g[self.color_index]
        }
    }
}

pub fn draw(app: &App, f: &mut Frame, area: Rect) {
    // Keep the normal top padding; startup uses the last rows to sit against the input.
    // A viewed child shows its own status, never the main session's startup failure.
    if area.height < 2 || (app.subagents.viewed_child().is_none() && failure::draw(app, f, area)) {
        return;
    }
    let Some(spans) = line::spans(app, area) else {
        return;
    };
    let rows = wrap::rows(spans, area.width);
    let height = (rows.len() as u16).min(area.height - 1);
    let top = match matches!(app.session.status, Status::Starting | Status::Failed) {
        true => area.bottom() - height,
        false => area.y + 1,
    };
    for (y, row) in (top..).zip(rows).take(usize::from(height)) {
        f.render_widget(
            row,
            Rect {
                y,
                height: 1,
                ..area
            },
        );
    }
}

/// The rows the loading line takes at `width`, its hints wrapped instead of cut.
pub fn rows(app: &App, width: u16) -> u16 {
    let area = Rect::new(0, 0, width, u16::MAX);
    line::spans(app, area).map_or(1, |spans| wrap::rows(spans, width).len().max(1) as u16)
}

/// Columns the loading line takes unwrapped, so the rail beside it never clips it; an idle loop line ellipsizes, so it claims none.
pub fn width(app: &App) -> u16 {
    let spans = line::spans(app, Rect::new(0, 0, 0, 1)).unwrap_or_default();
    let width: usize = spans.iter().map(Span::width).sum();
    u16::try_from(width).unwrap_or(u16::MAX)
}

/// `Ns`, then `NmNs`, then `NhNmNs`.
pub(super) fn format_elapsed(seconds: u64) -> String {
    if seconds < 60 {
        return format!("{seconds}s");
    }
    let (minutes, secs) = (seconds / 60, seconds % 60);
    if minutes < 60 {
        return format!("{minutes}m{secs}s");
    }
    let (hours, mins) = (minutes / 60, minutes % 60);
    format!("{hours}h{mins}m{secs}s")
}

#[cfg(test)]
mod tests;
