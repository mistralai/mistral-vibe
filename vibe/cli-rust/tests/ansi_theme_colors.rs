//! Textual-compatible semantic bindings for both ANSI themes.

use ratatui::style::Color;
use vibe_rs::ui::theme;

#[test]
fn semantic_colors_follow_textual() {
    theme::prepare_active("ansi-dark", "ansi-dark");
    assert_eq!(theme::toast_background(), Color::Black);
    assert_eq!(theme::selection_bg(), Color::LightBlue);
    assert_eq!(theme::selection_fg(), Color::Black);
    assert_eq!(theme::link_hover_style().fg, Some(Color::Black));
    assert_eq!(theme::link_hover_style().bg, Some(Color::LightBlue));

    theme::prepare_active("ansi-light", "ansi-light");
    assert_eq!(theme::toast_background(), Color::Gray);
    assert_eq!(theme::selection_bg(), Color::Cyan);
    assert_eq!(theme::selection_fg(), Color::White);
    assert_eq!(theme::link_hover_style().fg, Some(Color::Gray));
    assert_eq!(theme::link_hover_style().bg, Some(Color::LightBlue));

    for (name, expected) in [
        ("atom-one-dark", Color::Rgb(0x61, 0x68, 0x78)),
        ("atom-one-light", Color::Rgb(0xE1, 0xE1, 0xE1)),
        ("catppuccin-frappe", Color::Rgb(0x63, 0x69, 0x7F)),
        ("catppuccin-latte", Color::Rgb(0xE1, 0xE5, 0xEF)),
        ("catppuccin-macchiato", Color::Rgb(0x5A, 0x5E, 0x76)),
        ("catppuccin-mocha", Color::Rgb(0x56, 0x58, 0x6C)),
        ("dracula", Color::Rgb(0x41, 0x44, 0x53)),
        ("flexoki", Color::Rgb(0x38, 0x37, 0x36)),
        ("gruvbox", Color::Rgb(0x61, 0x5A, 0x56)),
        ("monokai", Color::Rgb(0x4F, 0x4E, 0x42)),
        ("nord", Color::Rgb(0x54, 0x5D, 0x70)),
        ("rose-pine", Color::Rgb(0x36, 0x32, 0x4B)),
        ("rose-pine-dawn", Color::Rgb(0xFF, 0xFE, 0xF6)),
        ("rose-pine-moon", Color::Rgb(0x4A, 0x45, 0x64)),
        ("solarized-dark", Color::Rgb(0x1D, 0x46, 0x53)),
        ("solarized-light", Color::Rgb(0xFF, 0xFD, 0xEA)),
        ("textual-dark", Color::Rgb(0x34, 0x3F, 0x49)),
        ("textual-light", Color::Rgb(0xE5, 0xE5, 0xE5)),
        ("tokyo-night", Color::Rgb(0x53, 0x59, 0x7A)),
    ] {
        theme::prepare_active(name, "ansi-dark");
        assert_eq!(theme::toast_background(), expected, "{name}");
    }
}
