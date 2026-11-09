//! `/context` gauge colors: one hue per category, the empty track, the buffer hatch.

use ratatui::style::Color;

use super::{active, blend, is_ansi};

/// Category hues in order: system prompt, tools, skills, conversation, other.
pub fn context_palette() -> [Color; 5] {
    if is_ansi() {
        return [
            Color::Blue,
            Color::Yellow,
            Color::Magenta,
            Color::Green,
            Color::Cyan,
        ];
    }
    let theme = active();
    [
        theme.primary,
        theme.warning,
        theme.accent,
        theme.success,
        theme.secondary,
    ]
}

/// The gauge's empty track: a faint lift off the background.
pub fn context_track() -> Color {
    if is_ansi() {
        return Color::DarkGray;
    }
    blend(super::background(), super::auto_contrast(), 0.10)
}

/// The `░` hatch over the track marking the auto-compaction buffer.
pub fn context_buffer() -> Color {
    if is_ansi() {
        return Color::Gray;
    }
    blend(super::background(), super::auto_contrast(), 0.35)
}
