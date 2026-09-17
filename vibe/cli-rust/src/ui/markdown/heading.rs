//! Markdown heading levels and Textual-compatible modifiers.

use pulldown_cmark::HeadingLevel;
use ratatui::style::Modifier;

pub(super) fn level(level: HeadingLevel) -> u8 {
    match level {
        HeadingLevel::H1 => 1,
        HeadingLevel::H2 => 2,
        HeadingLevel::H3 => 3,
        HeadingLevel::H4 => 4,
        HeadingLevel::H5 => 5,
        HeadingLevel::H6 => 6,
    }
}

pub(super) fn modifier(level: HeadingLevel) -> Modifier {
    match level {
        HeadingLevel::H1 | HeadingLevel::H4 => Modifier::BOLD,
        HeadingLevel::H2 | HeadingLevel::H6 => Modifier::UNDERLINED,
        _ => Modifier::empty(),
    }
}
