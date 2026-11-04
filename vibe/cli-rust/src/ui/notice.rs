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
    /// Set once painted, so a key typed before the first frame cannot hide it unseen.
    pub shown: bool,
}

impl Notice {
    /// A notice of `severity`, up until `until`, or until a key answers it when `None`.
    pub fn new(severity: Severity, text: &str, until: Option<UiInstant>) -> Self {
        Self {
            text: text.to_owned(),
            severity,
            until,
            shown: false,
        }
    }
}

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

/// Hide the inline notice.
pub fn clear(app: &mut App) {
    app.overlays.notice = None;
}

/// A key or paste answers a refusal it has seen: hide the painted sticky notice and `/config` status line.
pub fn answered(app: &mut App) {
    if app.config_screen.status.as_ref().is_some_and(|s| s.shown) {
        app.config_screen.status = None;
    }
    if app
        .overlays
        .notice
        .as_ref()
        .is_some_and(|notice| notice.until.is_none() && notice.shown)
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

pub fn width(app: &App) -> u16 {
    let Some(notice) = app
        .overlays
        .notice
        .as_ref()
        .filter(|notice| !expired(notice))
    else {
        return 0;
    };
    u16::try_from(Line::from(notice.text.as_str()).width()).unwrap_or(u16::MAX)
}

/// Render the inline notice right-aligned on the loading area's last row.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
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
    notice.shown = true;
    let text = crate::utils::text::ellipsize(&notice.text, usize::from(area.width));
    let line = Line::from(text).style(style(notice.severity));
    let row = Rect {
        y: area.y + area.height - 1,
        height: 1,
        ..area
    };
    f.render_widget(Paragraph::new(line).alignment(Alignment::Right), row);
}
