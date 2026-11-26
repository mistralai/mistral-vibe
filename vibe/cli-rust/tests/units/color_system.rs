//! Truecolor detection and the 256-color downgrade match rich's color system.

use ratatui::style::Color;
use vibe_rs::ui::theme::color_system::{advertises_truecolor, downgrade};

#[test]
fn only_a_truecolor_colorterm_advertises_truecolor() {
    assert!(advertises_truecolor(Some("truecolor")));
    assert!(advertises_truecolor(Some(" 24BIT ")));
    assert!(!advertises_truecolor(None));
    assert!(!advertises_truecolor(Some("")));
    assert!(!advertises_truecolor(Some("yes")));
}

#[test]
fn rgb_downgrades_to_richs_256_color_index() {
    // Expected indices come from rich `Color.downgrade(ColorSystem.EIGHT_BIT)`.
    let cases = [
        ((0xFF, 0x82, 0x05), 208),
        ((0xE1, 0x05, 0x00), 160),
        ((0x73, 0x9B, 0xC3), 104),
        ((0x12, 0x12, 0x12), 233),
        ((0xE0, 0xE0, 0xE0), 253),
        ((0x28, 0x2A, 0x36), 236),
        ((0x00, 0x00, 0x00), 16),
        ((0xFF, 0xFF, 0xFF), 231),
    ];
    for ((r, g, b), index) in cases {
        assert_eq!(
            downgrade(Color::Rgb(r, g, b)),
            Color::Indexed(index),
            "#{r:02X}{g:02X}{b:02X}"
        );
    }
}

#[test]
fn non_rgb_colors_pass_through() {
    for color in [Color::Reset, Color::Red, Color::Indexed(42)] {
        assert_eq!(downgrade(color), color);
    }
}
