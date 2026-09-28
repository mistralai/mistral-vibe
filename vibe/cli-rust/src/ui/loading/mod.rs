//! Loading area: a spinner, gradient status label, and interrupt hint.

mod snake;

use std::time::{Duration, Instant};

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::Frame;

use self::snake::Snake;
use super::theme;
use crate::app::{App, Status};

/// Default label; `reasoning` entries swap it for `THINKING_LOADING_STATUS`.
pub const DEFAULT_LOADING_STATUS: &str = "Generating";
pub const THINKING_LOADING_STATUS: &str = "Thinking";
pub const INITIALIZING_LOADING_STATUS: &str = "Initializing";
/// Label while a turn retries after a transient failure (Python `set_retrying`).
pub const RETRYING_LOADING_STATUS: &str = "Retrying";
/// Label for a slash command fetching in the background (`/whoami`).
pub const COMMAND_LOADING_STATUS: &str = "Loading";
pub const SHELL_LOADING_STATUS: &str = "Running command";

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
    pause_start: Option<Instant>,
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
            self.pause_start = Some(Instant::now());
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
            self.paused_total += pause_start.elapsed();
        }
        self.label = self
            .label_before_action_required
            .take()
            .unwrap_or_else(|| DEFAULT_LOADING_STATUS.to_owned());
    }

    /// Turn elapsed time, excluding whatever a blocking callback froze.
    fn elapsed(&self, since: Instant) -> Duration {
        let running = match self.pause_start {
            Some(pause_start) => pause_start.duration_since(since),
            None => since.elapsed(),
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
    // Keep the normal top padding; startup uses the last row to sit against the input.
    if area.height < 2 {
        return;
    }
    let content_y = if matches!(app.session.status, Status::Starting | Status::Failed) {
        area.y + area.height - 1
    } else {
        area.y + 1
    };
    let content = Rect {
        y: content_y,
        height: 1,
        ..area
    };

    // A crashed app server lands in `Failed`; its transcript notice explains
    // the failure, so the startup-error line stays out of the way.
    if app.session.status == Status::Failed && !app.server_closed {
        let message = app
            .session
            .startup_error
            .as_deref()
            .unwrap_or("unknown error");
        f.render_widget(
            Line::from(Span::styled(
                format!(" Failed to initialize: {message}"),
                Style::default().fg(theme::error()),
            )),
            content,
        );
        return;
    }
    if !app.view.command_loading
        && !matches!(
            app.session.status,
            Status::Starting | Status::Generating { .. }
        )
    {
        return;
    }
    // `--resume` goes straight to its picker; the engine still coming up behind
    // it is not something the user is waiting on.
    if app.session.status == Status::Starting && app.startup_picker_active() {
        return;
    }

    let anim = &app.view.loading;
    let color = |pos: usize| Style::default().fg(anim.color_at(pos));

    let mut spans: Vec<Span> = Vec::new();

    // Snake spinner glyph + gap.
    spans.push(Span::styled(anim.snake.render(), color(0)));
    spans.push(Span::raw(" "));

    // Label, one color-cycled span per character, then the trailing "… ".
    for (i, ch) in anim.label.chars().enumerate() {
        spans.push(Span::styled(ch.to_string(), color(1 + i)));
    }
    spans.push(Span::styled("… ", color(1 + anim.label.chars().count())));

    // Background slash commands have no timer or controls; manual shell commands do.
    if app.view.command_loading && app.session.shell_operation_id.is_none() {
        f.render_widget(Line::from(spans), content);
        return;
    }

    let muted = theme::muted_style();
    let key = Style::default()
        .fg(theme::primary())
        .add_modifier(Modifier::BOLD);
    // Startup keeps queue cancellation available without offering turn interruption.
    if app.session.status == Status::Starting {
        if !app.queue.is_empty() {
            spans.push(Span::styled("(", muted));
            spans.push(Span::styled("Ctrl+C", key));
            spans.push(Span::styled(" to cancel last queued message)", muted));
        }
        f.render_widget(Line::from(spans), content);
        return;
    }

    // Muted interrupt hint; status is always `Generating` here (draw returned above otherwise).
    let elapsed = match (app.session.shell_started_at, app.session.status) {
        (Some(since), _) => anim.elapsed(since),
        (None, Status::Generating { since }) => anim.elapsed(since),
        _ => Duration::default(),
    };
    spans.push(Span::styled(
        format!("({} ", format_elapsed(elapsed.as_secs())),
        muted,
    ));
    // With queued prompts Ctrl+C cancels the newest one instead of interrupting,
    // so the hint splits the two keys (Python `LoadingWidget._format_hint`).
    if app.session.active_turn_id.is_none() || app.queue.is_empty() {
        spans.push(Span::styled("Esc/Ctrl+C", key));
        spans.push(Span::styled(" to interrupt)", muted));
    } else {
        spans.push(Span::styled("Esc", key));
        spans.push(Span::styled(" to interrupt · ", muted));
        spans.push(Span::styled("Enter", key));
        spans.push(Span::styled(" to steer · ", muted));
        spans.push(Span::styled("Ctrl+C", key));
        spans.push(Span::styled(" to cancel last queued message)", muted));
    }

    // A callback is waiting for the user to stop typing (Python `.loading-debounce`).
    if app.question_app.pending.is_some()
        || (app.approval.active.is_none() && app.approval.has_pending())
    {
        spans.push(Span::styled(
            " typing detected, waiting…",
            Style::default().add_modifier(Modifier::DIM | Modifier::ITALIC),
        ));
    }

    f.render_widget(Line::from(spans), content);
}

/// Mirrors Python `_format_elapsed`: `Ns`, then `NmNs`, then `NhNmNs`.
fn format_elapsed(seconds: u64) -> String {
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
