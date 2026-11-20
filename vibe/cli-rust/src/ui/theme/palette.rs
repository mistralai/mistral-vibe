//! Color values as RGB channels, with named terminal colors resolved from the terminal's probed palette.

use ratatui::style::Color;

/// The channels of a color: its own when RGB, the probed palette value when it names one of the 16 terminal colors.
pub fn channels(color: Color) -> Option<[u8; 3]> {
    if let Color::Rgb(r, g, b) = color {
        return Some([r, g, b]);
    }
    crate::terminal_colors::palette(index(color)?)
}

/// The palette index a named terminal color stands for.
pub fn index(color: Color) -> Option<usize> {
    let named = [
        Color::Black,
        Color::Red,
        Color::Green,
        Color::Yellow,
        Color::Blue,
        Color::Magenta,
        Color::Cyan,
        Color::Gray,
        Color::DarkGray,
        Color::LightRed,
        Color::LightGreen,
        Color::LightYellow,
        Color::LightBlue,
        Color::LightMagenta,
        Color::LightCyan,
        Color::White,
    ];
    match color {
        Color::Indexed(index) => Some(usize::from(index)).filter(|&index| index < 16),
        color => named.iter().position(|&name| name == color),
    }
}
