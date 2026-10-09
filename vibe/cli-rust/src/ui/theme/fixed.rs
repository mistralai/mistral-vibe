//! Theme-independent colors, the only place outside the theme registry that spells a color value.

use ratatui::style::{Color, Style};

/// $mistral_orange #FF8205 — the prompt marker and brand accent.
pub const ORANGE: Color = Color::Rgb(0xFF, 0x82, 0x05);
pub const ORANGE_LIGHT: Color = Color::Rgb(0xFF, 0xAF, 0x00);
pub const ORANGE_DARK: Color = Color::Rgb(0xFA, 0x50, 0x0F);
pub const YELLOW: Color = Color::Rgb(0xFF, 0xD8, 0x00);
pub const RED: Color = Color::Rgb(0xE1, 0x05, 0x00);

/// Warm gradient the loading label cycles through (yellow → orange → red).
pub const LOADING_GRADIENT: [Color; 5] = [YELLOW, ORANGE_LIGHT, ORANGE, ORANGE_DARK, RED];

/// Onboarding heading (Python `$mistral_orange_title`); not the selection orange.
pub const ONBOARDING_TITLE: Color = Color::Rgb(0xff, 0x5a, 0x00);
/// Idle onboarding card border (Python `.onboarding-card` border).
pub const ONBOARDING_CARD_BORDER: Color = Color::Rgb(0x30, 0x30, 0x40);
/// Python `GRADIENT_COLORS`: the welcome banner highlight and the browser sign-in wait ramp.
pub const WELCOME_GRADIENT: [Color; 10] = [
    Color::Rgb(0xff, 0x6b, 0x00),
    Color::Rgb(0xff, 0x7b, 0x00),
    Color::Rgb(0xff, 0x8c, 0x00),
    Color::Rgb(0xff, 0x9d, 0x00),
    Color::Rgb(0xff, 0xae, 0x00),
    Color::Rgb(0xff, 0xbf, 0x00),
    Color::Rgb(0xff, 0xae, 0x00),
    Color::Rgb(0xff, 0x9d, 0x00),
    Color::Rgb(0xff, 0x8c, 0x00),
    Color::Rgb(0xff, 0x7b, 0x00),
];

/// SGR sequences for plain terminal output (rich's colors, by terminal depth).
pub mod sgr {
    /// rich's `[bold dark_orange]` on a 256-color-or-better terminal.
    pub const ORANGE_256: &str = "\x1b[1;38;5;208m";
    /// The same span downgraded on a 16-color terminal: dark_orange becomes bright red.
    pub const ORANGE_16: &str = "\x1b[1;91m";
    /// rich's `[red]`, unchanged at every color depth.
    pub const RED: &str = "\x1b[31m";
    /// rich's `[green]`.
    pub const GREEN: &str = "\x1b[32m";
    /// rich's `[yellow]`.
    pub const YELLOW: &str = "\x1b[33m";
}

/// Textual's `ansi_theme_dark` terminal palette.
pub mod monokai {
    use ratatui::style::Color;

    pub const RED: Color = Color::Rgb(244, 0, 95);
    pub const GREEN: Color = Color::Rgb(0x98, 0xE0, 0x24);
    pub const CYAN: Color = Color::Rgb(88, 209, 235);
    pub const BRIGHT_BLUE: Color = Color::Rgb(157, 101, 255);
    pub const BLACK: Color = Color::Rgb(26, 26, 26);
}

/// Textual's `ansi_theme_light` terminal palette.
pub mod alabaster {
    use ratatui::style::Color;

    pub const RED: Color = Color::Rgb(170, 55, 49);
    pub const GREEN: Color = Color::Rgb(0x44, 0x8C, 0x27);
    pub const CYAN: Color = Color::Rgb(0, 131, 178);
    pub const BRIGHT_WHITE: Color = Color::Rgb(247, 247, 247);
}

/// A Rich named color.
#[derive(Clone, Copy)]
pub enum Named {
    Red,
    Green,
    Cyan,
}

/// A Rich named color the way Textual renders it: the raw terminal color under an
/// ANSI theme, else mapped through Monokai (dark) or Alabaster (light).
pub fn named(color: Named) -> Color {
    match (super::is_ansi(), super::is_dark(), color) {
        (true, _, Named::Red) => Color::Red,
        (true, _, Named::Green) => Color::Green,
        (true, _, Named::Cyan) => Color::Cyan,
        (false, true, Named::Red) => monokai::RED,
        (false, true, Named::Green) => monokai::GREEN,
        (false, true, Named::Cyan) => monokai::CYAN,
        (false, false, Named::Red) => alabaster::RED,
        (false, false, Named::Green) => alabaster::GREEN,
        (false, false, Named::Cyan) => alabaster::CYAN,
    }
}

/// The input caret under an ANSI theme: black on the terminal's gray.
pub fn ansi_caret() -> Style {
    Style::default().fg(Color::Black).bg(Color::Gray)
}

/// A compact input's block caret over `base` (Textual `Input`: `$input-cursor-*`).
pub fn input_caret(base: Style) -> Style {
    if super::is_ansi() {
        return base.patch(ansi_caret());
    }
    base.fg(super::background()).bg(super::input_cursor_bg())
}
