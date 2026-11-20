use ratatui::backend::TestBackend;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::Terminal;

use super::{look_for, look_for_reply, paint, Look, UPPER, UPPER_STANDARD};
use crate::terminal_detect::{APPLE_TERMINAL, GHOSTTY, ITERM2, KITTY, UNKNOWN, WEZTERM};
use crate::ui::theme;

const PARENT: Color = Color::Indexed(42);
const PARENT_FG: Color = Color::Indexed(7);

#[test]
fn each_terminal_gets_the_look_it_can_draw() {
    assert_eq!(look_for(APPLE_TERMINAL, false), Look::Cells);
    for terminal in [GHOSTTY, KITTY, WEZTERM] {
        assert_eq!(look_for(terminal, false), Look::Glyphs(&UPPER));
    }
    assert_eq!(look_for(UNKNOWN, true), Look::Glyphs(&UPPER));
    for terminal in [ITERM2, UNKNOWN] {
        assert_eq!(look_for(terminal, false), Look::Glyphs(&UPPER_STANDARD));
    }
}

#[test]
fn inside_tmux_the_xtversion_reply_picks_the_look() {
    for reply in [
        "ghostty 1.4.0",
        "kitty(0.42.1)",
        "WezTerm 20240203",
        "rex 0.1.0-preview.19",
    ] {
        assert_eq!(look_for_reply(reply), Look::Glyphs(&UPPER), "{reply}");
    }
    for reply in ["iTerm2 3.7.7", "xterm.js(5.5.0)", "tmux 3.8"] {
        assert_eq!(
            look_for_reply(reply),
            Look::Glyphs(&UPPER_STANDARD),
            "{reply}"
        );
    }
    assert_eq!(look_for_reply(""), Look::Cells);
}

#[test]
fn glyph_thumbs_draw_eighth_edges_in_the_foreground() {
    let bar = theme::scrollbar();
    // The thumb runs from 5/8 into row 1 to 2/8 into row 4.
    for (upper, tail) in [(&UPPER, "🮂"), (&UPPER_STANDARD, "▔")] {
        let rows = [
            (" ", PARENT_FG, false),
            ("▃", bar, false),
            ("█", bar, false),
            ("█", bar, false),
            (tail, bar, false),
            (" ", PARENT_FG, false),
        ];
        assert_eq!(column(14, 4, Look::Glyphs(upper)), expected(rows));
    }
}

#[test]
fn cell_thumbs_snap_to_whole_reversed_rows() {
    let bar = theme::scrollbar();
    // The same thumb as above: 3/8 of row 1 and 2/8 of row 4 round away.
    let rows = [
        (" ", PARENT_FG, false),
        (" ", PARENT_FG, false),
        (" ", bar, true),
        (" ", bar, true),
        (" ", PARENT_FG, false),
        (" ", PARENT_FG, false),
    ];
    assert_eq!(column(14, 4, Look::Cells), expected(rows));
}

#[test]
fn a_row_aligned_thumb_has_no_partial_edges() {
    let bar = theme::scrollbar();
    let rows = [
        ("█", bar, false),
        ("█", bar, false),
        ("█", bar, false),
        (" ", PARENT_FG, false),
        (" ", PARENT_FG, false),
        (" ", PARENT_FG, false),
    ];
    assert_eq!(column(12, 0, Look::Glyphs(&UPPER)), expected(rows));
}

#[test]
fn a_fitting_document_paints_a_blank_column() {
    let rows = [(" ", PARENT_FG, false); 6];
    assert_eq!(column(6, 0, Look::Glyphs(&UPPER)), expected(rows));
}

fn expected(rows: [(&str, Color, bool); 6]) -> Vec<(String, Color, bool)> {
    rows.map(|(symbol, fg, reversed)| (symbol.to_string(), fg, reversed))
        .to_vec()
}

/// Each row of a 6-row bar painted over a reversed, dim `PARENT` cell, whose background every cell keeps and the blank track its foreground.
fn column(virtual_size: usize, position: usize, look: Look) -> Vec<(String, Color, bool)> {
    let area = Rect::new(0, 0, 1, 6);
    let mut terminal = Terminal::new(TestBackend::new(1, 6)).expect("terminal");
    terminal
        .draw(|f| {
            let parent = Style::default()
                .fg(PARENT_FG)
                .bg(PARENT)
                .add_modifier(Modifier::REVERSED | Modifier::DIM);
            f.buffer_mut().set_style(area, parent);
            paint(f, area, virtual_size, 6, position, look);
        })
        .expect("draw");
    let buffer = terminal.backend().buffer();
    (0..6)
        .map(|y| {
            let cell = &buffer[(0, y)];
            assert_eq!(cell.bg, PARENT);
            assert!(!cell.modifier.contains(Modifier::DIM));
            let reversed = cell.modifier.contains(Modifier::REVERSED);
            (cell.symbol().to_string(), cell.fg, reversed)
        })
        .collect()
}
