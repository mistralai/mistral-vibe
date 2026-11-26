//! Theme-independent colors, the only place outside the theme registry that spells a color value.

use ratatui::style::{Color, Style};

/// Brand Orange Medium — the prompt marker and brand accent.
pub const ORANGE: Color = Color::Rgb(0xFF, 0x82, 0x04);
/// Brand Orange Light.
pub const ORANGE_LIGHT: Color = Color::Rgb(0xFF, 0xAF, 0x01);
/// Brand Orange Strong.
pub const ORANGE_DARK: Color = Color::Rgb(0xFA, 0x50, 0x0F);
/// GitHub's merged purple, for merged PRs/MRs.
pub const MERGED: Color = Color::Rgb(0xA3, 0x71, 0xF7);

/// The brand orange ramp the loading label ping-pongs through.
pub const LOADING_GRADIENT: [Color; 3] = [ORANGE_LIGHT, ORANGE, ORANGE_DARK];

/// Onboarding heading; not the selection orange.
pub const ONBOARDING_TITLE: Color = ORANGE_DARK;
/// Idle onboarding card border (brand Black Light).
pub const ONBOARDING_CARD_BORDER: Color = Color::Rgb(0x34, 0x34, 0x46);
/// The welcome banner highlight and the browser sign-in wait ramp.
pub const WELCOME_GRADIENT: [Color; 4] = [ORANGE_DARK, ORANGE, ORANGE_LIGHT, ORANGE];

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

/// Terminal palette that resolves ANSI colors under dark themes.
pub mod monokai {
    use ratatui::style::Color;

    pub const GREEN: Color = Color::Rgb(0x98, 0xE0, 0x24);
}

/// Terminal palette that resolves ANSI colors under light themes.
pub mod alabaster {
    use ratatui::style::Color;

    pub const GREEN: Color = Color::Rgb(0x44, 0x8C, 0x27);
}

/// A Rich named color.
#[derive(Clone, Copy)]
pub enum Named {
    Green,
}

/// A Rich named color as rendered: the raw terminal color under an
/// ANSI theme, else mapped through Monokai (dark) or Alabaster (light).
pub fn named(color: Named) -> Color {
    match (super::is_ansi(), super::is_dark(), color) {
        (true, _, Named::Green) => Color::Green,
        (false, true, Named::Green) => monokai::GREEN,
        (false, false, Named::Green) => alabaster::GREEN,
    }
}

/// The input caret under an ANSI theme: black on the terminal's gray.
pub fn ansi_caret() -> Style {
    Style::default().fg(Color::Black).bg(Color::Gray)
}

/// A compact input's block caret over `base`.
pub fn input_caret(base: Style) -> Style {
    if super::is_ansi() {
        return base.patch(ansi_caret());
    }
    base.fg(super::background()).bg(super::input_cursor_bg())
}
