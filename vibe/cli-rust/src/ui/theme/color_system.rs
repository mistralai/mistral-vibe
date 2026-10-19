//! Terminal color depth (rich `Console._detect_color_system`) and rich's truecolor-to-256 downgrade.

use std::sync::OnceLock;

use ratatui::style::Color;

/// Whether the host terminal renders 24-bit SGR colors, detected once per process.
pub fn truecolor() -> bool {
    static TRUECOLOR: OnceLock<bool> = OnceLock::new();
    *TRUECOLOR.get_or_init(|| {
        cfg!(windows) || advertises_truecolor(std::env::var("COLORTERM").ok().as_deref())
    })
}

/// Rich's truecolor tier: `COLORTERM` is `truecolor` or `24bit`.
pub fn advertises_truecolor(colorterm: Option<&str>) -> bool {
    let colorterm = colorterm.unwrap_or_default().trim().to_lowercase();
    colorterm == "truecolor" || colorterm == "24bit"
}

/// `color` as the host terminal can render it: RGB falls back to the 256-color palette.
pub fn for_terminal(color: Color) -> Color {
    if truecolor() {
        color
    } else {
        downgrade(color)
    }
}

/// Rich `Color.downgrade(ColorSystem.EIGHT_BIT)`; non-RGB colors pass through.
pub fn downgrade(color: Color) -> Color {
    let Color::Rgb(r, g, b) = color else {
        return color;
    };
    let (red, green, blue) = (r as f64 / 255.0, g as f64 / 255.0, b as f64 / 255.0);
    let (max, min) = (red.max(green).max(blue), red.min(green).min(blue));
    let lightness = (max + min) / 2.0;
    let range = max - min;
    let saturation = if range == 0.0 {
        0.0
    } else if lightness <= 0.5 {
        range / (max + min)
    } else {
        range / (2.0 - max - min)
    };
    if saturation < 0.15 {
        return Color::Indexed(match (lightness * 25.0).round_ties_even() as u8 {
            0 => 16,
            25 => 231,
            gray => 231 + gray,
        });
    }
    let six = |c: u8| {
        let c = c as f64;
        let level = if c < 95.0 {
            c / 95.0
        } else {
            1.0 + (c - 95.0) / 40.0
        };
        level.round_ties_even() as u8
    };
    Color::Indexed(16 + 36 * six(r) + 6 * six(g) + six(b))
}
