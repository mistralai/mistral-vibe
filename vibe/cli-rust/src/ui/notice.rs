//! The self-hiding inline status line (Python `InlineNotice`).

use std::time::{Duration, Instant};

use ratatui::layout::{Alignment, Rect};
use ratatui::style::Modifier;
use ratatui::text::Line;
use ratatui::widgets::Paragraph;
use ratatui::Frame;

use super::theme;
use crate::app::{App, Notice, ToastSeverity};
use crate::clipboard;

/// Inline-notice timeout, Python `inline_notice.DEFAULT_NOTICE_TIMEOUT` (4s).
const NOTICE_TIMEOUT_SECS: u64 = 4;
/// Native-copy hint, Python `clipboard.NATIVE_COPY_HINT`.
const NATIVE_COPY_HINT: &str = "if paste fails, hold Shift (Option in iTerm2, Fn in Terminal.app) \
     while selecting for native copy";

/// Copy `text` and surface the same inline notice as Python.
pub fn copied(app: &mut App, text: &str) {
    let message = if clipboard::copy_to_clipboard(text) {
        "Copied to clipboard".to_string()
    } else {
        format!("Copied · {NATIVE_COPY_HINT}")
    };
    show(app, &message, NOTICE_TIMEOUT_SECS);
}

/// Copy a finalized selection only when autocopy is on, gating the notice the
/// same way for every surface. Python `on_mouse_up`: with autocopy off there is
/// no copy and no notice.
pub fn copy_if_autocopy(app: &mut App, text: &str) {
    if !text.is_empty() && app.session.startup_config.autocopy_to_clipboard {
        copied(app, text);
    }
}

/// Show an inline notice for `secs`, mirroring Python's `InlineNotice.show`.
pub fn show(app: &mut App, text: &str, secs: u64) {
    show_with_severity(app, text, secs, ToastSeverity::Information);
}

pub fn show_warning(app: &mut App, text: &str, secs: u64) {
    show_with_severity(app, text, secs, ToastSeverity::Warning);
}

fn show_with_severity(app: &mut App, text: &str, secs: u64, severity: ToastSeverity) {
    // The deadline is real time, like Python's `set_timer`, so captures line
    // up with the Python client's own notice expiry.
    let until = Instant::now() + Duration::from_secs(secs);
    app.overlays.notice = Some(Notice {
        text: text.to_string(),
        severity,
        until: Some(until),
    });
}

/// Show an inline notice that stays up until cleared (Python `timeout=None`).
pub fn pin(app: &mut App, text: &str) {
    app.overlays.notice = Some(Notice {
        text: text.to_string(),
        severity: ToastSeverity::Information,
        until: None,
    });
}

/// Hide the inline notice (Python `InlineNoticeCleared`).
pub fn clear(app: &mut App) {
    app.overlays.notice = None;
}

pub fn width(app: &App) -> u16 {
    let Some(notice) = &app.overlays.notice else {
        return 0;
    };
    if notice.until.is_some_and(|until| Instant::now() >= until) {
        return 0;
    }
    u16::try_from(Line::from(notice.text.as_str()).width()).unwrap_or(u16::MAX)
}

/// Render the inline notice right-aligned on the loading area's last row.
pub fn draw(app: &mut App, f: &mut Frame, area: Rect) {
    let Some(notice) = &app.overlays.notice else {
        return;
    };
    if notice.until.is_some_and(|until| Instant::now() >= until) {
        app.overlays.notice = None;
        return;
    }
    if area.height < 1 {
        return;
    }
    let style = match notice.severity {
        ToastSeverity::Information => theme::muted_style().remove_modifier(Modifier::BOLD),
        ToastSeverity::Warning => theme::text(theme::warning()),
        ToastSeverity::Error => theme::text(theme::error()),
    };
    let line = Line::from(notice.text.clone()).style(style);
    let row = Rect {
        y: area.y + area.height - 1,
        height: 1,
        ..area
    };
    f.render_widget(Paragraph::new(line).alignment(Alignment::Right), row);
}
