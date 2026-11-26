//! The terminal palette the startup probe reads, so named terminal colors get real values.

use ratatui::style::Color;
use vibe_rs::terminal_colors::{read, rgb_reply, QUERY};
use vibe_rs::ui::theme::{palette, selection_fg_of, ALL};

#[test]
fn palette_replies_read_as_rgb() {
    let response = b"\x1b]4;6;rgb:2a2a/a1a1/b3b3\x1b\\\x1b]4;12;rgb:5c/9f/ff\x07\x1b[?62;22c";
    let colors = read(response);
    assert_eq!(colors[6], Some([0x2a, 0xa1, 0xb3]));
    assert_eq!(colors[12], Some([0x5c, 0x9f, 0xff]));
    assert_eq!(colors[1], None);
}

#[test]
fn a_terminal_without_palette_replies_leaves_named_colors_unknown() {
    assert!(read(b"\x1b[?62;22c").iter().all(Option::is_none));
    assert_eq!(read(b"\x1b]4;6;rgb:zz/00/00\x07")[6], None);
}

#[test]
fn a_reply_takes_one_to_four_hex_digits_per_channel() {
    let prefix = b"\x1b]4;1;rgb:";
    assert_eq!(
        rgb_reply(b"\x1b]4;1;rgb:f/8/0\x07", prefix),
        Some([255, 136, 0])
    );
    assert_eq!(rgb_reply(b"\x1b]4;1;rgb:00000/0/0\x07", prefix), None);
    assert_eq!(rgb_reply(b"\x1b]4;1;rgb:0/0\x07", prefix), None);
}

#[test]
fn the_probe_asks_for_every_palette_color_the_copy_light_reads() {
    for theme in ALL {
        for color in [theme.selection_bg, theme.primary, selection_fg_of(theme)] {
            let Some(index) = palette::index(color) else {
                continue;
            };
            let query = format!("\x1b]4;{index};?");
            assert!(
                QUERY.windows(query.len()).any(|w| w == query.as_bytes()),
                "{}: palette color {index} is never asked for",
                theme.name
            );
        }
    }
}

#[test]
fn rgb_colors_keep_their_own_channels() {
    assert_eq!(palette::channels(Color::from([1, 2, 3])), Some([1, 2, 3]));
}
