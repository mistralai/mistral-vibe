//! Textual's built-in theme registry, resolved to concrete cell colors.

use std::sync::atomic::{AtomicUsize, Ordering};

use ratatui::style::Color;

/// A theme's palette, pre-resolved to the exact RGB each cell is painted with.
#[derive(Clone, Copy)]
pub struct Theme {
    pub name: &'static str,
    /// Light vs dark; selects the picker marker's ANSI-mapped green, matching Textual.
    pub dark: bool,
    pub background: Color,
    pub foreground: Color,
    pub primary: Color,
    /// `$accent`, which `$text-accent` (keywords) is tinted from.
    pub accent: Color,
    pub secondary: Color,
    pub warning: Color,
    pub error: Color,
    pub success: Color,
    /// `$text-success` / `$text-error` / the selection background: Textual
    /// composites these itself, and no `blend` reproduces them exactly.
    pub text_success: Color,
    pub text_error: Color,
    pub surface: Color,
    pub popup_border: Color,
    pub selection_bg: Color,
    pub block_cursor_bg: Color,
    pub block_cursor_fg: Color,
    /// Input caret block ($input-cursor-background; reverse-resolved for ansi themes).
    pub input_cursor_bg: Color,
    pub scrollbar: Color,
    pub scrollbar_bg: Color,
    /// `$panel-lighten-1`, used by toast backgrounds.
    pub panel_lighten_1: Color,
}

/// Build a `Color` from a `0xRRGGBB` literal, for terse theme tables.
const fn rgb(hex: u32) -> Color {
    Color::Rgb((hex >> 16) as u8, (hex >> 8) as u8, hex as u8)
}

const R: Color = Color::Reset;

/// The picker's virtual first option; resolves to a concrete theme at render.
pub const AUTO_NAME: &str = "auto";

/// Every built-in theme, ordered exactly as Textual's `sorted_theme_names()`
/// (light A→Z then dark A→Z). Values are generated from `ColorSystem.generate()`
/// so a switched theme renders cell-for-cell with the Python CLI.
#[rustfmt::skip]
pub const ALL: &[Theme] = &[
    Theme { name: "ansi-light", dark: false, background: R, foreground: R, primary: Color::Blue, accent: Color::Magenta, secondary: Color::Cyan, warning: Color::LightRed, error: Color::Red, success: Color::Green, text_success: Color::Green, text_error: Color::Red, surface: R, popup_border: R, selection_bg: Color::Cyan, block_cursor_bg: Color::Blue, block_cursor_fg: Color::White, input_cursor_bg: Color::Reset, scrollbar: Color::LightBlue, scrollbar_bg: Color::Gray, panel_lighten_1: Color::Gray },
    Theme { name: "atom-one-light", dark: false, background: rgb(0xFAFAFA), foreground: rgb(0x383A42), primary: rgb(0x4078F2), accent: rgb(0xBF9232), secondary: rgb(0xA626A4), warning: rgb(0xD7D938), error: rgb(0xF13F3F), success: rgb(0x6BF23F), text_success: rgb(0x479F29), text_error: rgb(0x9F2929), surface: rgb(0xE0E0E0), popup_border: rgb(0x85868B), selection_bg: rgb(0x9DB9F6), block_cursor_bg: rgb(0x4078F2), block_cursor_fg: rgb(0xE6EDFD), input_cursor_bg: rgb(0x383A42), scrollbar: rgb(0xA2B8E9), scrollbar_bg: rgb(0xE4E4E4), panel_lighten_1: rgb(0xE1E1E1) },
    Theme { name: "catppuccin-latte", dark: false, background: rgb(0xEFF1F5), foreground: rgb(0x4C4F69), primary: rgb(0x8839EF), accent: rgb(0xFE640B), secondary: rgb(0xDB8A78), warning: rgb(0xDE8E1D), error: rgb(0xD10F39), success: rgb(0x40A02B), text_success: rgb(0x2A691C), text_error: rgb(0x8A0925), surface: rgb(0xE6E9EF), popup_border: rgb(0x8D8FA1), selection_bg: rgb(0xBB95F2), block_cursor_bg: rgb(0x8839EF), block_cursor_fg: rgb(0xEFE5FC), input_cursor_bg: rgb(0x4C4F69), scrollbar: rgb(0xB89AE5), scrollbar_bg: rgb(0xD9DBDF), panel_lighten_1: rgb(0xE1E5EF) },
    Theme { name: "rose-pine-dawn", dark: false, background: rgb(0xFAF4ED), foreground: rgb(0x575279), primary: rgb(0x907AA9), accent: rgb(0xD7827E), secondary: rgb(0x286983), warning: rgb(0xE99D34), error: rgb(0xB4637A), success: rgb(0x56949F), text_success: rgb(0x386168), text_error: rgb(0x764150), surface: rgb(0xFFFAF3), popup_border: rgb(0x9892A7), selection_bg: rgb(0xC5B7CB), block_cursor_bg: rgb(0x575279), block_cursor_fg: rgb(0xFAF4ED), input_cursor_bg: rgb(0x575279), scrollbar: rgb(0xC2B6C4), scrollbar_bg: rgb(0xE4DED7), panel_lighten_1: rgb(0xFFFEF6) },
    Theme { name: "solarized-light", dark: false, background: rgb(0xFDF6E3), foreground: rgb(0x586E75), primary: rgb(0x268BD2), accent: rgb(0x6C71C4), secondary: rgb(0x2AA198), warning: rgb(0xCA4B16), error: rgb(0xDB322F), success: rgb(0x849900), text_success: rgb(0x576400), text_error: rgb(0x91211F), surface: rgb(0xEEE8D5), popup_border: rgb(0x9AA4A1), selection_bg: rgb(0x91C0DA), block_cursor_bg: rgb(0x268BD2), block_cursor_fg: rgb(0xE2EFF9), input_cursor_bg: rgb(0x586E75), scrollbar: rgb(0x99BECF), scrollbar_bg: rgb(0xE7E0CD), panel_lighten_1: rgb(0xFFFDEA) },
    Theme { name: "textual-light", dark: false, background: rgb(0xE0E0E0), foreground: rgb(0x1F1F1F), primary: rgb(0x004578), accent: rgb(0xFFA62B), secondary: rgb(0x0178D4), warning: rgb(0xFEA62B), error: rgb(0xB93C5B), success: rgb(0x4EBF71), text_success: rgb(0x337E4A), text_error: rgb(0x7A273C), surface: rgb(0xD8D8D8), popup_border: rgb(0x6C6C6C), selection_bg: rgb(0x7092AC), block_cursor_bg: rgb(0x004578), block_cursor_fg: rgb(0xDDE6ED), input_cursor_bg: rgb(0x1F1F1F), scrollbar: rgb(0x7994A9), scrollbar_bg: rgb(0xCACACA), panel_lighten_1: rgb(0xE5E5E5) },
    Theme { name: "ansi-dark", dark: true, background: R, foreground: R, primary: Color::Blue, accent: Color::Green, secondary: Color::Cyan, warning: Color::Yellow, error: Color::Red, success: Color::Green, text_success: Color::Green, text_error: Color::Red, surface: R, popup_border: R, selection_bg: Color::LightBlue, block_cursor_bg: Color::Gray, block_cursor_fg: Color::Black, input_cursor_bg: Color::Black, scrollbar: Color::Blue, scrollbar_bg: Color::Black, panel_lighten_1: Color::Black },
    Theme { name: "atom-one-dark", dark: true, background: rgb(0x282C34), foreground: rgb(0xABB2BF), primary: rgb(0x61AFEF), accent: rgb(0xA378C2), secondary: rgb(0xC678DD), warning: rgb(0xDDB25B), error: rgb(0xEF6262), success: rgb(0x62F062), text_success: rgb(0x97F597), text_error: rgb(0xF59797), surface: rgb(0x3B414D), popup_border: rgb(0x767C87), selection_bg: rgb(0x446D91), block_cursor_bg: rgb(0x61AFEF), block_cursor_fg: rgb(0x0C161F), input_cursor_bg: rgb(0xABB2BF), scrollbar: rgb(0x355674), scrollbar_bg: rgb(0x181C23), panel_lighten_1: rgb(0x616878) },
    Theme { name: "catppuccin-frappe", dark: true, background: rgb(0x303446), foreground: rgb(0xC6D0F5), primary: rgb(0xCA9EE6), accent: rgb(0xF4B8E4), secondary: rgb(0xEE9F76), warning: rgb(0xE4C890), error: rgb(0xE68284), success: rgb(0xA6D189), text_success: rgb(0xC4E0B1), text_error: rgb(0xEFACAD), surface: rgb(0x414559), popup_border: rgb(0x8A91AF), selection_bg: rgb(0x7C6895), block_cursor_bg: rgb(0xCA9EE6), block_cursor_fg: rgb(0x292C3C), input_cursor_bg: rgb(0xF2D5CF), scrollbar: rgb(0x63547B), scrollbar_bg: rgb(0x1F2435), panel_lighten_1: rgb(0x63697F) },
    Theme { name: "catppuccin-macchiato", dark: true, background: rgb(0x24273A), foreground: rgb(0xCAD3F5), primary: rgb(0xC6A0F6), accent: rgb(0xF5BDE6), secondary: rgb(0xF4A97F), warning: rgb(0xEED49F), error: rgb(0xED8796), success: rgb(0xA6DA95), text_success: rgb(0xC4E6B9), text_error: rgb(0xF3AFB9), surface: rgb(0x363A4F), popup_border: rgb(0x878EAA), selection_bg: rgb(0x746397), block_cursor_bg: rgb(0xC6A0F6), block_cursor_fg: rgb(0x1E2030), input_cursor_bg: rgb(0xF4DBD6), scrollbar: rgb(0x5B4D7B), scrollbar_bg: rgb(0x141729), panel_lighten_1: rgb(0x5A5E76) },
    Theme { name: "catppuccin-mocha", dark: true, background: rgb(0x181825), foreground: rgb(0xCDD6F4), primary: rgb(0xF5C2E7), accent: rgb(0xFAB387), secondary: rgb(0xCBA6F7), warning: rgb(0xFAE3B0), error: rgb(0xF28FAD), success: rgb(0xABE9B3), text_success: rgb(0xC7F0CC), text_error: rgb(0xF6B5C8), surface: rgb(0x313244), popup_border: rgb(0x848AA1), selection_bg: rgb(0x866C85), block_cursor_bg: rgb(0xF5C2E7), block_cursor_fg: rgb(0x1E1E2E), input_cursor_bg: rgb(0xF5E0DC), scrollbar: rgb(0x644E69), scrollbar_bg: rgb(0x040216), panel_lighten_1: rgb(0x56586C) },
    Theme { name: "dracula", dark: true, background: rgb(0x282A36), foreground: rgb(0xF8F8F2), primary: rgb(0xBD93F9), accent: rgb(0xFF79C6), secondary: rgb(0x6272A4), warning: rgb(0xFEB86C), error: rgb(0xFE5555), success: rgb(0x50FA7B), text_success: rgb(0x8BFBA7), text_error: rgb(0xFF8E8E), surface: rgb(0x2B2E3B), popup_border: rgb(0xA4A5A6), selection_bg: rgb(0x725E97), block_cursor_bg: rgb(0xBD93F9), block_cursor_fg: rgb(0x181320), input_cursor_bg: rgb(0xF8F8F2), scrollbar: rgb(0x5A4A79), scrollbar_bg: rgb(0x181A25), panel_lighten_1: rgb(0x414453) },
    Theme { name: "flexoki", dark: true, background: rgb(0x100F0F), foreground: rgb(0xFFFCF0), primary: rgb(0x205EA6), accent: rgb(0x9B76C8), secondary: rgb(0x24837B), warning: rgb(0xAC8301), error: rgb(0xAE3029), success: rgb(0x65800B), text_success: rgb(0x9AAB5D), text_error: rgb(0xCA7671), surface: rgb(0x1C1B1A), popup_border: rgb(0x9F9D96), selection_bg: rgb(0x17365A), block_cursor_bg: rgb(0x205EA6), block_cursor_fg: rgb(0xE2EAF3), input_cursor_bg: rgb(0xFFFCF0), scrollbar: rgb(0x0C2542), scrollbar_bg: rgb(0x000000), panel_lighten_1: rgb(0x383736) },
    Theme { name: "gruvbox", dark: true, background: rgb(0x282828), foreground: rgb(0xFBF1C7), primary: rgb(0x85A598), accent: rgb(0xFABD2F), secondary: rgb(0xA89A85), warning: rgb(0xFD8019), error: rgb(0xFA4934), success: rgb(0xB7BB26), text_success: rgb(0xD0D26F), text_error: rgb(0xFC8679), surface: rgb(0x3C3836), popup_border: rgb(0xA6A087), selection_bg: rgb(0x56665F), block_cursor_bg: rgb(0x85A598), block_cursor_fg: rgb(0xFBF1C7), input_cursor_bg: rgb(0xFBF1C7), scrollbar: rgb(0x43504B), scrollbar_bg: rgb(0x181818), panel_lighten_1: rgb(0x615A56) },
    Theme { name: "monokai", dark: true, background: rgb(0x272822), foreground: rgb(0xD6D6D6), primary: rgb(0xAE81FF), accent: rgb(0x66D9EF), secondary: rgb(0xF82672), warning: rgb(0xFC971F), error: rgb(0xF82672), success: rgb(0xA5E22E), text_success: rgb(0xC4EB75), text_error: rgb(0xFB6FA1), surface: rgb(0x2E2E2E), popup_border: rgb(0x797979), selection_bg: rgb(0x6A5490), block_cursor_bg: rgb(0xAE81FF), block_cursor_fg: rgb(0x161021), input_cursor_bg: rgb(0xD6D6D6), scrollbar: rgb(0x534270), scrollbar_bg: rgb(0x171812), panel_lighten_1: rgb(0x4F4E42) },
    Theme { name: "nord", dark: true, background: rgb(0x2E3440), foreground: rgb(0xD8DEE9), primary: rgb(0x88C0D0), accent: rgb(0xB48EAD), secondary: rgb(0x81A1C1), warning: rgb(0xEACB8B), error: rgb(0xBE616A), success: rgb(0xA3BE8C), text_success: rgb(0xC2D4B3), text_error: rgb(0xD4969C), surface: rgb(0x3B4252), popup_border: rgb(0x949AA5), selection_bg: rgb(0x5A7987), block_cursor_bg: rgb(0x88C0D0), block_cursor_fg: rgb(0x2E3440), input_cursor_bg: rgb(0xD8DEE9), scrollbar: rgb(0x48626F), scrollbar_bg: rgb(0x1E242F), panel_lighten_1: rgb(0x545D70) },
    Theme { name: "rose-pine", dark: true, background: rgb(0x191724), foreground: rgb(0xE0DEF4), primary: rgb(0xC4A7E7), accent: rgb(0xEBBCBA), secondary: rgb(0x31748F), warning: rgb(0xF5C177), error: rgb(0xEA6F92), success: rgb(0x9CCFD8), text_success: rgb(0xBDDFE5), text_error: rgb(0xF19FB7), surface: rgb(0x1F1D2E), popup_border: rgb(0x908EA0), selection_bg: rgb(0x6E5E85), block_cursor_bg: rgb(0xC4A7E7), block_cursor_fg: rgb(0x191724), input_cursor_bg: rgb(0xF4EDE8), scrollbar: rgb(0x524269), scrollbar_bg: rgb(0x060015), panel_lighten_1: rgb(0x36324B) },
    Theme { name: "rose-pine-moon", dark: true, background: rgb(0x232136), foreground: rgb(0xE0DEF4), primary: rgb(0xC4A7E7), accent: rgb(0xEA9A97), secondary: rgb(0x3E8FB0), warning: rgb(0xF5C177), error: rgb(0xEA6F92), success: rgb(0x9CCFD8), text_success: rgb(0xBDDFE5), text_error: rgb(0xF19FB7), surface: rgb(0x2A273F), popup_border: rgb(0x9492A8), selection_bg: rgb(0x73638E), block_cursor_bg: rgb(0xC4A7E7), block_cursor_fg: rgb(0x232136), input_cursor_bg: rgb(0xF4EDE8), scrollbar: rgb(0x594D72), scrollbar_bg: rgb(0x131125), panel_lighten_1: rgb(0x4A4564) },
    Theme { name: "solarized-dark", dark: true, background: rgb(0x002B36), foreground: rgb(0x839496), primary: rgb(0x268BD2), accent: rgb(0x6C71C4), secondary: rgb(0x2AA198), warning: rgb(0xCA4B16), error: rgb(0xDB322F), success: rgb(0x849900), text_success: rgb(0xAEBB56), text_error: rgb(0xE77775), surface: rgb(0x073642), popup_border: rgb(0x4E6A6F), selection_bg: rgb(0x125A83), block_cursor_bg: rgb(0x268BD2), block_cursor_fg: rgb(0xE2EFF9), input_cursor_bg: rgb(0x839496), scrollbar: rgb(0x0F476A), scrollbar_bg: rgb(0x001B25), panel_lighten_1: rgb(0x1D4653) },
    Theme { name: "textual-dark", dark: true, background: rgb(0x121212), foreground: rgb(0xE0E0E0), primary: rgb(0x0178D4), accent: rgb(0xFFA62B), secondary: rgb(0x004578), warning: rgb(0xFEA62B), error: rgb(0xB93C5B), success: rgb(0x4EBF71), text_success: rgb(0x8AD4A1), text_error: rgb(0xD17E92), surface: rgb(0x1E1E1E), popup_border: rgb(0x8D8D8D), selection_bg: rgb(0x094472), block_cursor_bg: rgb(0x0178D4), block_cursor_fg: rgb(0xDDEDF9), input_cursor_bg: rgb(0xE0E0E0), scrollbar: rgb(0x003054), scrollbar_bg: rgb(0x000000), panel_lighten_1: rgb(0x343F49) },
    Theme { name: "tokyo-night", dark: true, background: rgb(0x1A1B26), foreground: rgb(0xA9B1D6), primary: rgb(0xBB9AF7), accent: rgb(0xFF9E64), secondary: rgb(0x7AA2F7), warning: rgb(0xDFAF68), error: rgb(0xF6768E), success: rgb(0x9ECE6A), text_success: rgb(0xBEDE9C), text_error: rgb(0xF9A4B4), surface: rgb(0x24283B), popup_border: rgb(0x6F758F), selection_bg: rgb(0x6A5A8E), block_cursor_bg: rgb(0xBB9AF7), block_cursor_fg: rgb(0x181420), input_cursor_bg: rgb(0xA9B1D6), scrollbar: rgb(0x4F4270), scrollbar_bg: rgb(0x070817), panel_lighten_1: rgb(0x53597A) },
];

/// Index into `ALL` of the currently active theme. Seeded from config at startup.
static ACTIVE: AtomicUsize = AtomicUsize::new(11);
/// Process-local resolution of the persisted `auto` value, seeded dark.
static AUTO_RESOLVED: AtomicUsize = AtomicUsize::new(6);

/// The active theme's palette.
pub fn active() -> &'static Theme {
    &ALL[ACTIVE.load(Ordering::Relaxed).min(ALL.len() - 1)]
}

/// Textual's `$ansi-background`: black for dark and ANSI white for light.
pub fn ansi_background() -> Color {
    if active().dark {
        Color::Black
    } else {
        Color::Gray
    }
}

/// Textual's `$screen-selection-foreground`.
pub fn selection_fg() -> Color {
    match (active().background == Color::Reset, active().dark) {
        (true, true) => Color::Black,
        (true, false) => Color::White,
        (false, _) => active().foreground,
    }
}

/// Textual's `$panel-lighten-1`, used by toast backgrounds.
pub fn toast_background() -> Color {
    active().panel_lighten_1
}

/// Index into `ALL` of the active theme.
pub fn active_index() -> usize {
    ACTIVE.load(Ordering::Relaxed).min(ALL.len() - 1)
}

/// Point the active theme at `index` in `ALL` (live preview / selection).
pub fn set_active_index(index: usize) {
    if index < ALL.len() {
        ACTIVE.store(index, Ordering::Relaxed);
    }
}

/// Point `auto` at the detected light or dark ANSI palette for this process.
pub(super) fn set_auto(name: &str) {
    if let Some(index) = index_of(name) {
        AUTO_RESOLVED.store(index, Ordering::Relaxed);
    }
}

/// The `ALL` index for a theme name; resolving is pure after startup detection.
pub fn resolve(name: &str) -> Option<usize> {
    if name == AUTO_NAME {
        return Some(AUTO_RESOLVED.load(Ordering::Relaxed).min(ALL.len() - 1));
    }
    index_of(name)
}

fn index_of(name: &str) -> Option<usize> {
    ALL.iter().position(|t| t.name == name)
}
