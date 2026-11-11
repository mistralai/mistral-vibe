//! The inline status line above the input: confirmations fade, refusals stay until the next key.

use std::time::Duration;

use crate::clock::UiInstant;

use ratatui::layout::{Alignment, Rect};
use ratatui::style::Modifier;
use ratatui::text::Line;
use ratatui::widgets::Paragraph;
use ratatui::Frame;

use super::theme;
use crate::app::{App, Severity};
use crate::clipboard;

/// An inline status line, shown until `until`, or until the next key when `None`.
pub struct Notice {
    pub text: String,
    pub severity: Severity,
    pub until: Option<UiInstant>,
    /// How long after its first paint a key cannot hide it.
    pub hold: Duration,
    /// When it was first painted, so a key typed before the first frame cannot hide it unseen.
    pub shown_at: Option<UiInstant>,
}

impl Notice {
    /// A notice of `severity`, up until `until`, or until a key answers it when `None`.
    pub fn new(severity: Severity, text: &str, until: Option<UiInstant>) -> Self {
        Self {
            text: crate::utils::text::bounded_message(text).into_owned(),
            severity,
            until,
            hold: Duration::ZERO,
            shown_at: None,
        }
    }

    /// Record a paint the user could see.
    pub fn mark_shown(&mut self) {
        self.shown_at.get_or_insert_with(crate::clock::now);
    }

    /// Whether it has been painted where the user could see it.
    pub fn shown(&self) -> bool {
        self.shown_at.is_some()
    }

    /// A failure wraps under the loading row so its text reads; other notices end in an ellipsis.
    fn is_failure(&self) -> bool {
        !self.hold.is_zero()
    }

    /// Whether a key may hide it: painted, and held long enough to be read.
    pub fn answerable(&self) -> bool {
        self.shown_at
            .is_some_and(|at| crate::clock::since(at) >= self.hold)
    }
}

/// How long a failure that can land while the user types stays up despite keys.
pub const FAILURE_HOLD: Duration = Duration::from_millis(1500);

/// Most rows a wrapped failure takes; the rest ends in an ellipsis.
pub const MAX_ROWS: usize = 3;

/// How long a confirmation stays up.
const CONFIRMATION_SECS: u64 = 4;

/// Copy `text` and surface the copy notice.
pub fn copied(app: &mut App, text: &str) {
    crate::telemetry::user_copied_text(app, text);
    let verified = clipboard::copy_to_clipboard(text);
    copied_as(app, "Copied to clipboard", "Copied", verified);
}

/// The copy confirmation: `label` once verified, else `short` with the paste hint.
pub fn copied_as(app: &mut App, label: &str, short: &str, verified: bool) {
    let message = match verified {
        true => label.to_owned(),
        false => format!("{short} · {}", clipboard::unverified_hint()),
    };
    show(app, Severity::Information, &message, CONFIRMATION_SECS);
}

/// Copy a finalized selection only when autocopy is on, gating the notice the
/// same way for every surface: with autocopy off there is no copy and no notice.
pub fn copy_if_autocopy(app: &mut App, text: &str) {
    if !text.is_empty() && app.session.startup_config.autocopy_to_clipboard {
        copied(app, text);
    }
}

/// A notice of `severity` that fades after `secs`: confirmations, and failures the next action retries.
pub fn show(app: &mut App, severity: Severity, text: &str, secs: u64) {
    // The deadline is wall-clock time, not a frame count.
    let until = crate::clock::now() + Duration::from_secs(secs);
    app.overlays.notice = Some(Notice::new(severity, text, Some(until)));
}

/// A notice of `severity` that stays up until a key answers it.
pub fn show_sticky(app: &mut App, severity: Severity, text: &str) {
    app.overlays.notice = Some(Notice::new(severity, text, None));
}

/// A failure that stays until a key answers it, but never before `FAILURE_HOLD` has passed on screen.
pub fn show_failure(app: &mut App, severity: Severity, text: &str) {
    let notice = Notice {
        hold: FAILURE_HOLD,
        ..Notice::new(severity, text, None)
    };
    app.overlays.notice = Some(notice);
}

/// Hide the inline notice.
pub fn clear(app: &mut App) {
    app.overlays.notice = None;
}

/// A key or paste answers a refusal it has seen: hide the painted sticky notice and `/config` status line.
pub fn answered(app: &mut App) {
    if app
        .config_screen
        .status
        .as_ref()
        .is_some_and(Notice::answerable)
    {
        app.config_screen.status = None;
    }
    if app
        .overlays
        .notice
        .as_ref()
        .is_some_and(|notice| notice.until.is_none() && notice.answerable())
    {
        app.overlays.notice = None;
    }
}

/// The color a notice of `severity` paints in, shared with `/config`'s status line.
pub fn style(severity: Severity) -> ratatui::style::Style {
    match severity {
        Severity::Information => theme::muted_style().remove_modifier(Modifier::BOLD),
        Severity::Warning => theme::text(theme::warning()),
        Severity::Error => theme::text(theme::error()),
    }
}

fn expired(notice: &Notice) -> bool {
    notice
        .until
        .is_some_and(|until| crate::clock::now() >= until)
}

fn visible(app: &App) -> Option<&Notice> {
    app.overlays
        .notice
        .as_ref()
        .filter(|notice| !expired(notice))
}

pub fn width(app: &App) -> u16 {
    visible(app).map_or(0, |notice| {
        u16::try_from(Line::from(notice.text.as_str()).width()).unwrap_or(u16::MAX)
    })
}

/// `text` word-wrapped to `width`, at most `max` rows, the last cut with an ellipsis when more remain.
fn wrapped(text: &str, width: u16, max: usize) -> Vec<String> {
    let width = usize::from(width);
    let max = max.max(1);
    let mut lines = crate::utils::text::wrap_hard(text, width);
    if lines.len() > max {
        let rest = lines[max - 1..].join(" ");
        lines.truncate(max - 1);
        lines.push(crate::utils::text::ellipsize(&rest, width));
    }
    lines
}

/// Rows of the full-width block a failure too wide for its `slot` on the loading row wraps into; 0 when it fits.
pub fn block_rows(app: &App, slot: u16, width: u16) -> u16 {
    visible(app)
        .filter(|notice| {
            notice.is_failure() && Line::from(notice.text.as_str()).width() > usize::from(slot)
        })
        .map_or(0, |notice| {
            wrapped(&notice.text, width, MAX_ROWS).len() as u16
        })
}

/// Render the notice right-aligned at the bottom of `area`: one ellipsized row, or the wrapped block.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect, block: bool) {
    // The release notes cover the loading area: the notice waits for them to close.
    let covered = app.release_notes.open;
    let Some(notice) = &mut app.overlays.notice else {
        return;
    };
    if expired(notice) {
        app.overlays.notice = None;
        return;
    }
    if area.height < 1 || area.width < 1 {
        return;
    }
    if !covered {
        notice.mark_shown();
    }
    let lines: Vec<Line> = match block {
        true => wrapped(
            &notice.text,
            area.width,
            usize::from(area.height).min(MAX_ROWS),
        ),
        false => vec![crate::utils::text::ellipsize(
            &notice.text,
            usize::from(area.width),
        )],
    }
    .into_iter()
    .map(|line| Line::from(line).style(style(notice.severity)))
    .collect();
    let height = (lines.len() as u16).min(area.height);
    let rows = Rect {
        y: area.y + area.height - height,
        height,
        ..area
    };
    f.render_widget(Paragraph::new(lines).alignment(Alignment::Right), rows);
}
