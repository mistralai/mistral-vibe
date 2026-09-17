//! Settled status rows: the compact indicator and the client checkpoint message.

use ratatui::text::{Line, Span};

use super::super::super::{pulse, theme};

/// Python `CompactMessage` / `StatusMessage`: a single status row — pulse
/// spinner while in progress, ✓ on success, ✕ on error. No border. A blank
/// line separates it from the preceding command echo.
pub(super) fn push_compact_status(
    lines: &mut Vec<Line<'static>>,
    text: &str,
    in_progress: bool,
    pulse_frame: usize,
) {
    let (glyph, color) = if in_progress {
        (pulse::glyph(pulse_frame).to_string(), theme::foreground())
    } else if text.starts_with("Error:") {
        ("✕".to_string(), theme::error())
    } else {
        ("✓".to_string(), theme::status_ready())
    };
    lines.push(Line::from(""));
    lines.push(Line::from(vec![
        Span::styled(format!("{glyph} "), theme::text(color)),
        Span::styled(text.to_string(), theme::text(theme::foreground())),
    ]));
}

/// A settled status message: the `✓` indicator then its text, whose extra lines
/// align under the first one (Python's `StatusMessage` horizontal layout).
pub(super) fn push_checkpoint(lines: &mut Vec<Line<'static>>, message: &str) {
    lines.push(Line::from(""));
    for (index, text) in message.lines().enumerate() {
        let marker = if index == 0 { "✓ " } else { "  " };
        lines.push(Line::from(vec![
            Span::styled(marker, theme::text(theme::status_ready())),
            Span::styled(text.to_string(), theme::text(theme::foreground())),
        ]));
    }
}
