//! Loading area: a spinner, gradient status label, and key hints.

pub mod easter_eggs;
pub mod failure;
mod hint;
mod line;
mod snake;
mod wrap;

pub(crate) use hint::queue as queue_hints;

use std::time::Duration;

use crate::clock::UiInstant;

use ratatui::layout::Rect;
use ratatui::style::{Color, Style};
use ratatui::text::{Line, Span};
use ratatui::Frame;

use self::snake::Snake;
use super::rng::Rng;
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
/// The loader of a session the Sessions page is opening.
pub const OPENING_SESSION_STATUS: &str = "Opening session";
pub const SHELL_LOADING_STATUS: &str = "Running command";
/// Still glyph for later work: an agent waiting on its background work.
const WAITING_GLYPH: &str = "◷";

/// Spinner glyph + label gradient state, recreated at the start of each turn.
pub struct LoadingAnim {
    snake: Snake,
    /// Current label text (`Generating`/`Thinking`), mirroring `_base_status`.
    label: String,
    /// An easter egg shown in place of `label`, rolled whenever `label` changes.
    egg: Option<&'static str>,
    eggs: easter_eggs::Mode,
    rng: Rng,
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
        let mut anim = Self {
            snake: Snake::default(),
            label: String::new(),
            egg: None,
            eggs: easter_eggs::mode(),
            rng: Rng::default(),
            action_required: None,
            label_before_action_required: None,
            paused_total: Duration::default(),
            pause_start: None,
            color_index: 0,
            direction: 1,
            progress: 0,
        };
        anim.assign(DEFAULT_LOADING_STATUS);
        anim
    }
}

impl LoadingAnim {
    /// Current label text (`Generating`/`Thinking`/`Retrying`), never an easter egg.
    pub fn label(&self) -> &str {
        &self.label
    }

    pub fn shown(&self) -> &str {
        self.egg.unwrap_or(&self.label)
    }

    /// Change the label, rolling a new egg; setting the same label keeps the current text.
    fn assign(&mut self, label: &str) {
        if self.label == label {
            return;
        }
        self.label = label.to_owned();
        self.egg = easter_eggs::hatch(self.eggs, &mut self.rng, label);
    }

    /// Set the label without disturbing the spinner or gradient.
    pub fn set_label(&mut self, label: &str) {
        if let Some(action_required) = &self.action_required {
            if action_required != label {
                self.label_before_action_required = Some(label.to_owned());
            }
            return;
        }
        self.assign(label);
    }

    /// Show a blocking user-action label and freeze the elapsed counter.
    pub fn begin_action_required(&mut self, label: &str) {
        if self.action_required.is_none() {
            self.label_before_action_required = Some(self.label.clone());
            self.pause_start = Some(crate::clock::now());
        }
        self.action_required = Some(label.to_owned());
        self.assign(label);
    }

    /// Resume progress once the callback has been answered.
    pub fn end_action_required(&mut self) {
        if self.action_required.take().is_none() {
            return;
        }
        if let Some(pause_start) = self.pause_start.take() {
            self.paused_total += crate::clock::since(pause_start);
        }
        let label = self
            .label_before_action_required
            .take()
            .unwrap_or_else(|| DEFAULT_LOADING_STATUS.to_owned());
        self.assign(&label);
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
        1 + self.shown().chars().count() + 1
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

    /// The spinner and `label` in this animation's moving gradient, one span
    /// per character, then `suffix` (`…`, or `… ` before more of the line).
    pub fn spans(&self, label: &str, suffix: &str) -> Vec<Span<'static>> {
        let color = |pos: usize| Style::default().fg(self.color_at(pos));
        let mut spans = vec![Span::styled(self.snake.render(), color(0)), Span::raw(" ")];
        for (i, ch) in label.chars().enumerate() {
            spans.push(Span::styled(ch.to_string(), color(1 + i)));
        }
        spans.push(Span::styled(
            suffix.to_owned(),
            color(1 + label.chars().count()),
        ));
        spans
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
    let Some(spans) = line::spans(app, area.width) else {
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
    line::spans(app, width).map_or(1, |spans| wrap::rows(spans, width).len().max(1) as u16)
}

/// Columns the loading line takes unwrapped, so the rail beside it never clips it.
pub fn width(app: &App) -> u16 {
    spans_width(&line::measured(app, None).unwrap_or_default())
}

/// The columns a waiting line shrinks to (`◷ Waiting…`); `None` for a line that does not shrink.
pub fn narrow_width(app: &App) -> Option<u16> {
    let narrow = spans_width(&line::measured(app, Some(0)).unwrap_or_default());
    (narrow < width(app)).then_some(narrow)
}

/// Columns `spans` take on one row.
fn spans_width(spans: &[Span]) -> u16 {
    let width: usize = spans.iter().map(Span::width).sum();
    u16::try_from(width).unwrap_or(u16::MAX)
}

/// Columns of the loading line's widest part that cannot wrap or shrink, so a narrow column never clips its label.
pub fn min_width(app: &App) -> u16 {
    let spans = line::measured(app, Some(0)).unwrap_or_default();
    let widest = wrap::rows(spans, 0)
        .iter()
        .map(Line::width)
        .max()
        .unwrap_or(0);
    u16::try_from(widest).unwrap_or(u16::MAX)
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
